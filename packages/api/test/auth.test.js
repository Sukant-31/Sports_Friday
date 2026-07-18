import { test } from 'node:test';
import assert from 'node:assert/strict';
import { authCookieOptions } from '../src/services/authService.js';

// Pure checks that don't need a database. CRUD/integration tests that hit
// Postgres run in CI against the service container (see .github/workflows).

test('auth cookie is httpOnly and sameSite=lax', () => {
  const opts = authCookieOptions();
  assert.equal(opts.httpOnly, true);
  assert.equal(opts.sameSite, 'lax');
  assert.equal(opts.path, '/');
});
