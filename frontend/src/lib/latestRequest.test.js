import test from 'node:test';
import assert from 'node:assert/strict';
import { createLatestRequest } from './latestRequest.js';

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test('older successful reads cannot replace a newer response', async () => {
  const reads = createLatestRequest();
  const old = reads.start();
  const delayed = deferred();
  let data;
  const pending = delayed.promise.then((value) => { if (old.isCurrent()) data = value; });
  const current = reads.start();
  assert.equal(old.signal.aborted, true);
  if (current.isCurrent()) data = 'new match';
  delayed.resolve('old match');
  await pending;
  assert.equal(data, 'new match');
});

test('stale failure and completion cannot replace current error/loading state', async () => {
  const reads = createLatestRequest();
  const old = reads.start();
  const delayed = deferred();
  let error = null, loading = true;
  const pending = delayed.promise.catch((e) => {
    if (old.isCurrent()) error = e.message;
  }).finally(() => { if (old.isCurrent()) loading = false; });
  reads.start();
  delayed.reject(new Error('old failure'));
  await pending;
  assert.equal(error, null);
  assert.equal(loading, true);
});

test('route cleanup/unmount invalidates outstanding requests', () => {
  const reads = createLatestRequest();
  const request = reads.start();
  reads.cancel();
  assert.equal(request.signal.aborted, true);
  assert.equal(request.isCurrent(), false);
  assert.equal(reads.start().isCurrent(), true);
});

test('a mutation invalidates an earlier refresh and old mutation completion', () => {
  const reads = createLatestRequest(), mutations = createLatestRequest();
  const refresh = reads.start();
  const mutation = mutations.start();
  reads.cancel();
  assert.equal(refresh.isCurrent(), false);
  assert.equal(mutation.isCurrent(), true);
  mutations.cancel();
  assert.equal(mutation.isCurrent(), false);
});
