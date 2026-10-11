import test from 'node:test';
import assert from 'node:assert/strict';
import { createTeamSearchController, requestTeamSearch } from './teamSearch.js';

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness(request, timeoutMs = 1000) {
  const state = { loading: false, data: null, error: null };
  const events = [];
  const controller = createTeamSearchController({
    request,
    timeoutMs,
    onStart() { state.loading = true; state.error = null; events.push('start'); },
    onSuccess(data) { state.data = data; events.push('success'); },
    onError(error) { state.error = error.message; events.push('error'); },
    onFinish() { state.loading = false; events.push('finish'); },
  });
  return { controller, events, state };
}

test('successful and empty searches both leave the loading state', async () => {
  for (const teams of [[{ id: 'barcelona' }], []]) {
    const search = harness(async () => ({ teams, warning: null }));
    await search.controller.start('barcelona');
    assert.deepEqual(search.state, {
      loading: false, data: { teams, warning: null }, error: null,
    });
    assert.deepEqual(search.events, ['start', 'success', 'finish']);
  }
});

test('network failure leaves loading false and allows a retry', async () => {
  let attempt = 0;
  const search = harness(async () => {
    attempt += 1;
    if (attempt === 1) throw new Error('Network unavailable');
    return { teams: [{ id: 'recovered' }], warning: null };
  });
  await search.controller.start('barcelona');
  assert.equal(search.state.loading, false);
  assert.equal(search.state.error, 'Network unavailable');
  await search.controller.start('barcelona');
  assert.equal(search.state.loading, false);
  assert.deepEqual(search.state.data.teams, [{ id: 'recovered' }]);

});

for (const [status, body, message] of [
  [429, { error: 'Too many requests' }, 'Too many requests'],
  [500, {}, 'Request failed (500)'],
  [422, { detail: [{ loc: ['query', 'q'], msg: 'Search is invalid' }] },
    'query.q: Search is invalid'],
]) {
  test(`HTTP ${status} search failure preserves the API error`, async (t) => {
    t.mock.method(globalThis, 'fetch', async () =>
      new Response(JSON.stringify(body), { status }));
    await assert.rejects(requestTeamSearch('barcelona'), (error) =>
      error.status === status && error.message === message);
  });
}

test('a request timeout clears loading and a later search succeeds', async () => {
  let attempt = 0;
  const search = harness(() => {
    attempt += 1;
    return attempt === 1 ? new Promise(() => {})
      : Promise.resolve({ teams: [{ id: 'retry' }], warning: null });
  }, 5);
  await search.controller.start('barcelona');
  assert.equal(search.state.loading, false);
  assert.equal(search.state.error, 'Team search timed out. Please try again.');
  await search.controller.start('barcelona');
  assert.equal(search.state.loading, false);
  assert.deepEqual(search.state.data.teams, [{ id: 'retry' }]);
});

test('bar to barcelona to barcelona fc cancels prior requests and newest wins', async () => {
  const pending = [];
  const search = harness((query, { signal }) => {
    const item = { query, signal, ...deferred() };
    pending.push(item);
    return item.promise;
  });
  const bar = search.controller.start('bar');
  const barcelona = search.controller.start('barcelona');
  const full = search.controller.start('barcelona fc');
  assert.equal(pending[0].signal.aborted, true);
  assert.equal(pending[1].signal.aborted, true);
  pending[2].resolve({ teams: [{ id: 'newest' }], warning: null });
  await full;
  const newest = structuredClone(search.state);
  pending[1].resolve({ teams: [{ id: 'middle' }], warning: null });
  pending[0].resolve({ teams: [{ id: 'oldest' }], warning: null });
  await Promise.all([bar, barcelona]);
  assert.deepEqual(search.state, newest);
  assert.deepEqual(search.state.data.teams, [{ id: 'newest' }]);
  assert.equal(search.state.loading, false);
});

test('clear cancels an active search and a new search works', async () => {
  let firstSignal;
  let calls = 0;
  const search = harness((_query, { signal }) => {
    calls += 1;
    if (calls === 1) { firstSignal = signal; return new Promise(() => {}); }
    return Promise.resolve({ teams: [{ id: 'after-clear' }], warning: null });
  });
  const abandoned = search.controller.start('barcelona');
  search.controller.cancel();
  await abandoned;
  assert.equal(firstSignal.aborted, true);
  assert.equal(search.state.loading, false);
  await search.controller.start('arsenal');
  assert.deepEqual(search.state.data.teams, [{ id: 'after-clear' }]);
  assert.equal(search.state.loading, false);
});

test('team request preserves authentication cookie and forwards cancellation', async (t) => {
  const controller = new AbortController();
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/teams/search?q=Barcelona%20FC');
    assert.equal(options.credentials, 'include');
    assert.equal(options.signal, controller.signal);
    return new Response(JSON.stringify({ teams: [], warning: null }));
  });
  assert.deepEqual(await requestTeamSearch('Barcelona FC', { signal: controller.signal }),
    { teams: [], warning: null });
});
