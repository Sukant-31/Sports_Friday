import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';
import { createLatestRequest } from '../lib/latestRequest.js';

test('A to B navigation resets React identity before effects; B renders normal loading', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
  try {
    const { MatchDetailRoute: route } = await server.ssrLoadModule('/src/pages/MatchDetail.jsx');
    const a = route({ id: 'A' }), b = route({ id: 'B' });
    assert.equal(a.type, b.type);
    assert.equal(a.key, 'A');
    assert.equal(b.key, 'B');
    assert.equal(b.props.id, 'B');
    assert.equal(route({ id: 'B' }).key, b.key);
    const html = renderToStaticMarkup(React.createElement(MemoryRouter, null, b));
    assert.match(html, /skeleton/);
    assert.doesNotMatch(html, /Match not found|Could not load this match/);
  } finally {
    await server.close();
  }
});

for (const outcome of ['success', 'error', 'not-found']) {
  test(`pending A ${outcome} after B succeeds cannot overwrite B data/error/loading`, async () => {
    const reads = createLatestRequest();
    const a = reads.start();
    let resolve, reject;
    const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
    let state;
    const completion = pending.then((data) => {
      if (a.isCurrent()) state.data = data;
    }).catch((error) => {
      if (a.isCurrent()) {
        if (error.status === 404) state.notFound = true;
        else state.error = error.message;
      }
    }).finally(() => { if (a.isCurrent()) state.loading = false; });
    reads.cancel(); // keyed route unmount cleans up the old request
    const b = reads.start();
    if (b.isCurrent()) state = { data: { match: { id: 'B' } }, error: null,
      notFound: false, loading: false };
    if (outcome === 'success') resolve({ match: { id: 'A' } });
    else reject(Object.assign(new Error('A failed'), { status: outcome === 'not-found' ? 404 : 500 }));
    await completion;
    assert.deepEqual(state, { data: { match: { id: 'B' } }, error: null,
      notFound: false, loading: false });
  });
}

test('normal single-match completion and retry remain current', () => {
  const reads = createLatestRequest();
  const request = reads.start();
  assert.equal(request.isCurrent(), true);
  const retry = reads.start();
  assert.equal(request.isCurrent(), false);
  assert.equal(retry.isCurrent(), true);
  assert.equal(retry.signal.aborted, false);
});
