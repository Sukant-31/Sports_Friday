import { test } from 'node:test';
import assert from 'node:assert/strict';
import { z } from 'zod';

// Validates the request-schema contract without touching the DB. Full CRUD
// integration lives in CI against a throwaway Postgres.
const createSchema = z.object({
  teamId: z.string().uuid(),
  notifyGoals: z.boolean().optional(),
});

test('subscription create schema rejects a non-uuid teamId', () => {
  assert.throws(() => createSchema.parse({ teamId: 'not-a-uuid' }));
});

test('subscription create schema accepts a valid payload', () => {
  const parsed = createSchema.parse({
    teamId: '11111111-1111-1111-1111-111111111111',
    notifyGoals: false,
  });
  assert.equal(parsed.notifyGoals, false);
});
