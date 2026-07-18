import { query } from '../pool.js';

// Idempotency ledger. Returns the inserted row, or null if this exact event
// (by dedup_key) was already recorded — the caller then skips enqueueing.
export async function recordEventIfNew({ matchId, teamId, type, detail, dedupKey }) {
  const { rows } = await query(
    `INSERT INTO match_events (match_id, team_id, type, detail, dedup_key)
     VALUES ($1, $2, $3, $4, $5)
     ON CONFLICT (dedup_key) DO NOTHING
     RETURNING id, match_id, team_id, type, detail`,
    [matchId, teamId, type, detail ?? {}, dedupKey],
  );
  return rows[0] ?? null; // null => duplicate, already handled
}

// Baseline of already-emitted event dedup keys for a match — used by the
// poller on a Redis cache miss so it doesn't re-fire historical events.
export async function listEventKeysForMatch(matchId) {
  const { rows } = await query(
    `SELECT dedup_key FROM match_events WHERE match_id = $1`,
    [matchId],
  );
  return rows.map((r) => r.dedup_key);
}
