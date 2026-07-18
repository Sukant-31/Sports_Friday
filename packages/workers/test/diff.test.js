import { test } from 'node:test';
import assert from 'node:assert/strict';
import { diffMatch } from '../src/diff.js';
import { dedupKey } from '../src/dedupKey.js';

const base = {
  status: 'live',
  minute: 10,
  homeExternalId: '100',
  awayExternalId: '200',
  homeScore: 0,
  awayScore: 0,
  events: [],
};

test('no change yields no events', () => {
  assert.deepEqual(diffMatch(base, { ...base }), []);
});

test('home score increment yields exactly one goal for the home team', () => {
  const next = { ...base, homeScore: 1, minute: 23 };
  const events = diffMatch(base, next);
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'goal');
  assert.equal(events[0].teamExternalId, '100');
  assert.equal(events[0].homeScore, 1);
});

test('kickoff fires when status goes scheduled -> live', () => {
  const prev = { ...base, status: 'scheduled' };
  const events = diffMatch(prev, { ...base, status: 'live' });
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'kickoff');
});

test('full_time fires when status goes live -> finished', () => {
  const events = diffMatch(base, { ...base, status: 'finished' });
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'full_time');
});

test('first sight of a match (no prev) emits no phantom goals', () => {
  const next = { ...base, homeScore: 2, awayScore: 1 };
  const events = diffMatch(null, next);
  // status is already 'live' from before default 'scheduled' -> kickoff only
  assert.deepEqual(events.map((e) => e.type), ['kickoff']);
});

test('re-processing the same snapshot emits nothing (idempotent diff)', () => {
  const next = { ...base, homeScore: 1 };
  const first = diffMatch(base, next);
  const second = diffMatch(next, next);
  assert.equal(first.length, 1);
  assert.equal(second.length, 0);
});

test('dedupKey is stable for the same goal and distinct across teams', () => {
  const goal = { type: 'goal', teamExternalId: '100', homeScore: 1, awayScore: 0 };
  assert.equal(dedupKey('fx1', goal), dedupKey('fx1', goal));
  const away = { type: 'goal', teamExternalId: '200', homeScore: 1, awayScore: 1 };
  assert.notEqual(dedupKey('fx1', goal), dedupKey('fx1', away));
});
