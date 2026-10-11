import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from './api.js';

test('API preserves a meaningful 404 error and status', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response(
    JSON.stringify({ detail: 'Match not found' }), { status: 404 },
  ));
  await assert.rejects(api.matchDetail('missing'), (error) =>
    error.message === 'Match not found' && error.status === 404);
});

test('API forwards cached-data warnings and sends the auth cookie', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/teams/search?q=Arsenal');
    assert.equal(options.credentials, 'include');
    return new Response(JSON.stringify({ teams: [], warning: 'Live search unavailable' }));
  });
  assert.deepEqual(await api.searchTeams('Arsenal'), {
    teams: [], warning: 'Live search unavailable',
  });
});

test('API handles non-JSON server errors', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response('Gateway error', { status: 502 }));
  await assert.rejects(api.liveMatches(), /Request failed \(502\)/);
});

test('API handles empty success responses', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 204 }));
  assert.equal(await api.unsubscribe('follow'), null);
});

test('push deregistration sends only the current endpoint with authentication', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/push/subscribe');
    assert.equal(options.method, 'DELETE');
    assert.equal(options.credentials, 'include');
    assert.deepEqual(JSON.parse(options.body), { endpoint: 'current-browser' });
    return new Response(null, { status: 204 });
  });
  assert.equal(await api.unregisterPush('current-browser'), null);
});

test('API surfaces validation details without reflecting submitted password values', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({
    detail: [{ loc: ['body', 'password'], msg: 'Value error, Password must be at most 72 UTF-8 bytes',
      input: 'must-not-display-this-secret' }],
  }), { status: 422 }));
  await assert.rejects(api.signup('example@example.com', 'dummy-password'), (error) =>
    error.status === 422 && error.message.includes('password:') &&
    error.message.includes('72 UTF-8 bytes') && !error.message.includes('must-not-display'));
});

test('match-detail and settings reads forward cancellation without changing cookies', async (t) => {
  const controller = new AbortController();
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    assert.equal(options.signal, controller.signal);
    assert.equal(options.credentials, 'include');
    return new Response(JSON.stringify({ subscriptions: [] }));
  });
  await api.matchDetail('match', { signal: controller.signal });
  await api.listSubscriptions({ signal: controller.signal });
});
