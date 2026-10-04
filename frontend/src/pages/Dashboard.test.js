import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { createServer } from 'vite';

let server, Dashboard, api;
before(async () => {
  server = await createServer({
    server: { middlewareMode: true, hmr: { port: 0 } }, appType: 'custom',
  });
  ({ default: Dashboard } = await server.ssrLoadModule('/src/pages/Dashboard.jsx'));
  ({ api } = await server.ssrLoadModule('/src/lib/api.js'));
});
after(async () => { await server?.close(); });

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// Exercise Dashboard's actual hooks, effects and handlers without a DOM or new
// test dependency. Child components need not mount to test request state updates.
function mount(t) {
  const state = [], refs = [], effects = [], updates = [], intervals = [];
  let stateIndex = 0, refIndex = 0;
  const previousDocument = globalThis.document;
  globalThis.document = { hidden: false, addEventListener() {}, removeEventListener() {} };
  t.mock.method(globalThis, 'setInterval', (callback, ms) => {
    intervals.push({ callback, ms });
    return intervals.length;
  });
  t.mock.method(globalThis, 'clearInterval', () => {});
  const dispatcher = {
    useContext: () => ({ user: { id: 'dashboard-user' }, loading: false }),
    useState(initial) {
      const index = stateIndex++;
      state[index] = initial;
      return [initial, (value) => {
        state[index] = typeof value === 'function' ? value(state[index]) : value;
        updates.push(index);
      }];
    },
    useRef(initial) {
      const index = refIndex++;
      refs[index] = { current: initial };
      return refs[index];
    },
    useCallback: (callback) => callback,
    useEffect: (effect) => effects.push(effect),
  };
  const internal = React.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentDispatcher;
  const previous = internal.current;
  let tree;
  internal.current = dispatcher;
  try { tree = Dashboard(); } finally { internal.current = previous; }
  function findRefresh(element) {
    if (!element || typeof element !== 'object') return null;
    if (element.type === 'button' && element.props.children === 'Refresh') return element.props.onClick;
    for (const child of React.Children.toArray(element.props?.children)) {
      const found = findRefresh(child);
      if (found) return found;
    }
    return null;
  }
  const refresh = findRefresh(tree);
  assert.equal(typeof refresh, 'function');
  const cleanup = effects.map((effect) => effect());
  let mounted = true;
  const unmount = () => {
    if (!mounted) return;
    mounted = false;
    cleanup.forEach((fn) => fn?.());
  };
  t.after(() => {
    unmount();
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
  });
  return {
    refresh, unmount, updates, intervals,
    snapshot: () => ({ matches: state[1], error: state[2], warning: state[3],
      updatedAt: state[5], refreshing: state[6] }),
  };
}

function requests(t) {
  const pending = [];
  t.mock.method(globalThis, 'fetch', (url, options) => {
    assert.equal(url, '/api/matches/live');
    assert.equal(options.credentials, 'include');
    assert.ok(options.signal instanceof AbortSignal);
    const request = { ...deferred(), signal: options.signal };
    pending.push(request);
    return request.promise;
  });
  return pending;
}

function success(request, name) {
  request.resolve(new Response(JSON.stringify({ matches: [{ id: name }], warning: name })));
}

async function flush() {
  await new Promise((resolve) => setImmediate(resolve));
}

test('newer dashboard refresh wins even when an aborted request returns later', async (t) => {
  const pending = requests(t), dashboard = mount(t);
  assert.equal(pending.length, 1); // existing mount request
  assert.equal(dashboard.intervals.find((item) => item.ms === 15000).ms, 15000);
  const current = dashboard.refresh();
  assert.equal(pending.length, 2);
  assert.equal(pending[0].signal.aborted, true);
  success(pending[1], 'new');
  await current;
  const expected = dashboard.snapshot();
  success(pending[0], 'old'); // model a transport that ignores cancellation
  await flush();
  assert.deepEqual(dashboard.snapshot(), expected);
  assert.deepEqual(expected.matches, [{ id: 'new' }]);
  assert.equal(expected.warning, 'new');
  assert.equal(expected.error, null);
  assert.equal(expected.refreshing, false);
  assert.equal(typeof expected.updatedAt, 'number');
});

test('obsolete request errors and completion cannot alter current loading or error', async (t) => {
  const pending = requests(t), dashboard = mount(t);
  const current = dashboard.refresh();
  pending[0].reject(new DOMException('Aborted', 'AbortError'));
  await flush();
  assert.equal(dashboard.snapshot().error, null);
  assert.equal(dashboard.snapshot().refreshing, true);
  success(pending[1], 'current');
  await current;
  assert.equal(dashboard.snapshot().refreshing, false);
});

for (const outcome of ['success', 'error']) {
  test(`unmount cancels a pending dashboard request and ignores late ${outcome}`, async (t) => {
    const pending = requests(t), dashboard = mount(t);
    dashboard.unmount();
    assert.equal(pending[0].signal.aborted, true);
    const before = dashboard.snapshot(), updateCount = dashboard.updates.length;
    if (outcome === 'success') success(pending[0], 'late');
    else pending[0].reject(new Error('Late failure'));
    await flush();
    assert.deepEqual(dashboard.snapshot(), before);
    assert.equal(dashboard.updates.length, updateCount);
  });
}

test('normal refresh and failed refresh preserve last-known data and allow retry', async (t) => {
  const pending = requests(t), dashboard = mount(t);
  assert.equal(dashboard.snapshot().matches, null);
  success(pending[0], 'initial');
  await flush();
  const initial = dashboard.snapshot();
  assert.deepEqual(initial.matches, [{ id: 'initial' }]);
  assert.equal(initial.refreshing, false);
  const failed = dashboard.refresh();
  pending[1].resolve(new Response('Gateway error', { status: 502 }));
  await failed;
  assert.deepEqual(dashboard.snapshot(), { ...initial,
    error: 'Request failed (502)', refreshing: false });
  const retry = dashboard.refresh();
  success(pending[2], 'recovered');
  await retry;
  assert.deepEqual(dashboard.snapshot().matches, [{ id: 'recovered' }]);
  assert.equal(dashboard.snapshot().error, null);
  assert.equal(dashboard.snapshot().refreshing, false);
  assert.equal(pending.length, 3);
});
