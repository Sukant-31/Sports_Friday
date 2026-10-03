import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from './lib/api.js';
import { authenticateWithPushCleanup, logoutWithPushCleanup } from './pushLifecycle.js';
import { enablePushNotifications } from './registerSW.js';

function browser(t) {
  const calls = [];
  let subscription;
  const rows = new Map([['A', new Set(['browser-A', 'other-device'])], ['B', new Set()]]);
  let account = 'A';
  subscription = {
    endpoint: 'browser-A',
    async unsubscribe() { calls.push('unsubscribe'); subscription = null; return true; },
    toJSON() { return { endpoint: this.endpoint, keys: { p256dh: 'key', auth: 'auth' } }; },
  };
  const registration = { pushManager: {
    async getSubscription() { return subscription; },
    async subscribe() {
      subscription = { endpoint: 'browser-B', toJSON() {
        return { endpoint: this.endpoint, keys: { p256dh: 'key', auth: 'auth' } };
      } };
      return subscription;
    },
  } };
  for (const [name, value] of Object.entries({
    navigator: { serviceWorker: { getRegistration: async () => registration,
      register: async () => {}, ready: Promise.resolve(registration) } },
    window: { PushManager: {} }, Notification: { requestPermission: async () => 'granted' },
  })) {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { configurable: true, value });
    t.after(() => descriptor ? Object.defineProperty(globalThis, name, descriptor) : delete globalThis[name]);
  }
  t.mock.method(api, 'me', async () => ({ user: { id: account } }));
  t.mock.method(api, 'unregisterPush', async (endpoint) => {
    calls.push('delete'); rows.get(account).delete(endpoint);
  });
  t.mock.method(api, 'logout', async () => { calls.push('logout'); account = null; });
  t.mock.method(api, 'login', async () => { calls.push('login'); account = 'B'; return { user: { id: 'B' } }; });
  t.mock.method(api, 'registerPush', async ({ endpoint }) => {
    calls.push('register'); assert.equal(account, 'B'); rows.get(account).add(endpoint);
  });
  t.mock.method(api, 'vapidKey', async () => ({ key: 'AQ' }));
  return { calls, rows, subscription, getAccount: () => account };
}

test('logout detaches only this browser before clearing authentication', async (t) => {
  const state = browser(t);
  await logoutWithPushCleanup();
  assert.deepEqual(state.calls, ['delete', 'unsubscribe', 'logout']);
  assert.deepEqual([...state.rows.get('A')], ['other-device']);
});

test('backend deletion failure keeps session and local subscription, and reports an error', async (t) => {
  const state = browser(t);
  t.mock.method(api, 'unregisterPush', async () => { throw new Error('Network failed'); });
  await assert.rejects(logoutWithPushCleanup(), /Could not disconnect/);
  assert.equal(state.getAccount(), 'A');
  assert.deepEqual(state.calls, []);
});

for (const mode of ['throws', 'returns false']) {
  test(`browser unsubscribe ${mode} blocks logout explicitly`, async (t) => {
    const state = browser(t);
    state.subscription.unsubscribe = async () => {
      if (mode === 'throws') throw new Error('Browser failed');
      return false;
    };
    await assert.rejects(logoutWithPushCleanup(), /Could not disable/);
    assert.equal(state.getAccount(), 'A');
    assert.deepEqual(state.calls, ['delete']);
    assert.deepEqual([...state.rows.get('A')], ['other-device']);
  });
}

test('A logout then B login and enable never reuses A endpoint', async (t) => {
  const state = browser(t);
  await logoutWithPushCleanup();
  await authenticateWithPushCleanup(api.login, 'b@example.com', 'password');
  assert.ok(!state.calls.includes('register'));
  await enablePushNotifications();
  assert.deepEqual([...state.rows.get('A')], ['other-device']);
  assert.deepEqual([...state.rows.get('B')], ['browser-B']);
  assert.ok(state.calls.indexOf('login') < state.calls.indexOf('register'));
});

test('direct login switching cleans current cookie-backed account before new login', async (t) => {
  const state = browser(t);
  await authenticateWithPushCleanup(api.login, 'b@example.com', 'password');
  assert.deepEqual(state.calls, ['delete', 'unsubscribe', 'login']);
  await enablePushNotifications();
  assert.deepEqual([...state.rows.get('A')], ['other-device']);
  assert.deepEqual([...state.rows.get('B')], ['browser-B']);
});

test('cleanup failure blocks direct switching before new authentication', async (t) => {
  const state = browser(t);
  t.mock.method(api, 'unregisterPush', async () => { throw new Error('Network failed'); });
  await assert.rejects(authenticateWithPushCleanup(api.login), /Could not disconnect/);
  assert.equal(state.getAccount(), 'A');
  assert.ok(!state.calls.includes('login'));
});

test('expired session revokes local endpoint without deleting another account rows', async (t) => {
  const state = browser(t);
  t.mock.method(api, 'me', async () => { throw Object.assign(new Error('Expired'), { status: 401 }); });
  await authenticateWithPushCleanup(api.login);
  assert.deepEqual(state.calls, ['unsubscribe', 'login']);
  assert.ok(state.rows.get('A').has('browser-A'));
  await enablePushNotifications();
  assert.deepEqual([...state.rows.get('B')], ['browser-B']);
});

test('session-check server failure blocks switching without discarding ownership', async (t) => {
  const state = browser(t);
  t.mock.method(api, 'me', async () => { throw Object.assign(new Error('Unavailable'), { status: 503 }); });
  await assert.rejects(authenticateWithPushCleanup(api.login), /Unavailable/);
  assert.deepEqual(state.calls, []);
});

test('failed local cleanup can be retried after backend deletion already succeeded', async (t) => {
  const state = browser(t);
  const unsubscribe = state.subscription.unsubscribe;
  state.subscription.unsubscribe = async () => { throw new Error('Temporary failure'); };
  await assert.rejects(logoutWithPushCleanup(), /Could not disable/);
  state.subscription.unsubscribe = unsubscribe;
  await logoutWithPushCleanup();
  assert.deepEqual(state.calls, ['delete', 'delete', 'unsubscribe', 'logout']);
  assert.deepEqual([...state.rows.get('A')], ['other-device']);
});

test('logout without an existing browser subscription does not delete other devices', async (t) => {
  const state = browser(t);
  await state.subscription.unsubscribe();
  state.calls.length = 0;
  await logoutWithPushCleanup();
  assert.deepEqual(state.calls, ['logout']);
  assert.ok(state.rows.get('A').has('other-device'));
});

test('signup also cleans a previous account before setting new authentication', async (t) => {
  const state = browser(t);
  const signup = async () => { state.calls.push('signup'); return { user: { id: 'B' } }; };
  await authenticateWithPushCleanup(signup);
  assert.deepEqual(state.calls, ['delete', 'unsubscribe', 'signup']);
});

test('expired-session logout revokes locally on deletion 401 then clears authentication', async (t) => {
  const state = browser(t);
  t.mock.method(api, 'unregisterPush', async (endpoint) => {
    assert.equal(endpoint, 'browser-A');
    state.calls.push('delete-401');
    throw Object.assign(new Error('Expired'), { status: 401 });
  });
  await logoutWithPushCleanup();
  assert.deepEqual(state.calls, ['delete-401', 'unsubscribe', 'logout']);
  assert.equal(state.getAccount(), null);
  assert.deepEqual([...state.rows.get('A')], ['browser-A', 'other-device']);
  assert.deepEqual([...state.rows.get('B')], []);
});

test('session expiry between verification and deletion allows safe account switching', async (t) => {
  const state = browser(t);
  t.mock.method(api, 'me', async () => {
    state.calls.push('verified-A');
    return { user: { id: 'A' } };
  });
  t.mock.method(api, 'unregisterPush', async () => {
    state.calls.push('delete-401');
    throw Object.assign(new Error('Expired during deletion'), { status: 401 });
  });
  await authenticateWithPushCleanup(api.login);
  assert.deepEqual(state.calls, ['verified-A', 'delete-401', 'unsubscribe', 'login']);
  assert.deepEqual([...state.rows.get('A')], ['browser-A', 'other-device']);
  await enablePushNotifications();
  assert.deepEqual([...state.rows.get('B')], ['browser-B']);
  assert.ok(state.rows.get('A').has('browser-A'));
});

test('401 does not bypass a local unsubscribe failure or clear authentication', async (t) => {
  const state = browser(t);
  t.mock.method(api, 'unregisterPush', async () => {
    throw Object.assign(new Error('Expired'), { status: 401 });
  });
  state.subscription.unsubscribe = async () => { throw new Error('Browser failed'); };
  await assert.rejects(logoutWithPushCleanup(), /Could not disable/);
  assert.equal(state.getAccount(), 'A');
  assert.ok(!state.calls.includes('logout'));
  assert.ok(state.rows.get('A').has('browser-A'));
});

for (const status of [undefined, 403, 500, 503]) {
  test(`deletion failure ${status ?? 'network'} still blocks logout and switching`, async (t) => {
    const state = browser(t);
    t.mock.method(api, 'unregisterPush', async () => {
      throw Object.assign(new Error('Deletion failed'), { status });
    });
    await assert.rejects(logoutWithPushCleanup(), /Could not disconnect/);
    await assert.rejects(authenticateWithPushCleanup(api.login), /Could not disconnect/);
    assert.deepEqual(state.calls, []);
    assert.equal(state.getAccount(), 'A');
    assert.deepEqual([...state.rows.get('A')], ['browser-A', 'other-device']);
    assert.deepEqual([...state.rows.get('B')], []);
  });
}
