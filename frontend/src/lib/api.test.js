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
