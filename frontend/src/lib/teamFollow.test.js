import test from 'node:test';
import assert from 'node:assert/strict';
import { createTeamFollowController } from './teamFollow.js';

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness(request) {
  const state = { followed: false, warning: null, error: null };
  const controller = createTeamFollowController({
    request,
    onOptimistic() { state.followed = true; state.error = null; },
    onSuccess(_team, { subscription }) { state.warning = subscription.warning; },
    onRollback() { state.followed = false; },
    onError(error) { state.error = error.message; },
  });
  return { controller, state };
}

test('follow updates the UI before the server responds and reconciles success', async () => {
  const pending = deferred();
  const follow = harness(() => pending.promise);
  const completion = follow.controller.follow({ id: 'barcelona' });
  assert.equal(follow.state.followed, true);
  assert.equal(follow.state.error, null);
  pending.resolve({ subscription: { warning: 'Fixtures will refresh shortly' } });
  assert.equal(await completion, true);
  assert.equal(follow.state.warning, 'Fixtures will refresh shortly');
});

test('failed follow rolls optimistic state back and remains retryable', async () => {
  let attempt = 0;
  const follow = harness(async () => {
    attempt += 1;
    if (attempt === 1) throw new Error('Could not follow team');
    return { subscription: { warning: null } };
  });
  assert.equal(await follow.controller.follow({ id: 'arsenal' }), false);
  assert.deepEqual(follow.state, {
    followed: false, warning: null, error: 'Could not follow team',
  });
  assert.equal(await follow.controller.follow({ id: 'arsenal' }), true);
  assert.deepEqual(follow.state, { followed: true, warning: null, error: null });
});

test('rapid repeated clicks issue only one follow request', async () => {
  const pending = deferred();
  let requests = 0;
  const follow = harness(() => { requests += 1; return pending.promise; });
  const first = follow.controller.follow({ id: 'milan' });
  const duplicate = follow.controller.follow({ id: 'milan' });
  assert.equal(follow.state.followed, true);
  assert.equal(await duplicate, false);
  assert.equal(requests, 1);
  pending.resolve({ subscription: { warning: null } });
  assert.equal(await first, true);
  assert.equal(requests, 1);
});

test('an already-followed team never issues another request', async () => {
  let requests = 0;
  const follow = harness(async () => { requests += 1; });
  assert.equal(await follow.controller.follow({ id: 'milan', followed: true }), false);
  assert.equal(requests, 0);
});
