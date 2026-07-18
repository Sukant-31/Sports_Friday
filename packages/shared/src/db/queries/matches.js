import { query } from '../pool.js';

// Matches worth polling: not finished AND followed by at least one user.
export async function findPollableMatches() {
  const { rows } = await query(
    `SELECT DISTINCT m.id, m.external_id, m.status, m.home_score, m.away_score,
            m.home_team_id, m.away_team_id, m.starts_at, m.last_polled_at
     FROM matches m
     WHERE m.status IN ('scheduled', 'live')
       AND (
         EXISTS (SELECT 1 FROM subscriptions s WHERE s.team_id = m.home_team_id)
         OR EXISTS (SELECT 1 FROM subscriptions s WHERE s.team_id = m.away_team_id)
       )`,
  );
  return rows;
}

export async function findMatchByExternalId(externalId) {
  const { rows } = await query(
    `SELECT * FROM matches WHERE external_id = $1`,
    [externalId],
  );
  return rows[0] ?? null;
}

export async function upsertMatch({
  externalId,
  homeTeamId,
  awayTeamId,
  status,
  homeScore,
  awayScore,
  startsAt,
}) {
  const { rows } = await query(
    `INSERT INTO matches
       (external_id, home_team_id, away_team_id, status, home_score, away_score, starts_at)
     VALUES ($1, $2, $3, $4, $5, $6, $7)
     ON CONFLICT (external_id) DO UPDATE
       SET home_team_id = EXCLUDED.home_team_id,
           away_team_id = EXCLUDED.away_team_id
     RETURNING *`,
    [externalId, homeTeamId, awayTeamId, status, homeScore ?? 0, awayScore ?? 0, startsAt ?? null],
  );
  return rows[0];
}

// Persist the latest observed state after a poll tick.
export async function updateMatchState(id, { status, homeScore, awayScore }) {
  const { rows } = await query(
    `UPDATE matches
     SET status = $2, home_score = $3, away_score = $4, last_polled_at = now()
     WHERE id = $1
     RETURNING *`,
    [id, status, homeScore, awayScore],
  );
  return rows[0];
}

// Live matches for the teams a given user follows (dashboard).
export async function findLiveMatchesForUser(userId) {
  const { rows } = await query(
    `SELECT DISTINCT m.id, m.external_id, m.status, m.home_score, m.away_score,
            ht.name AS home_team, at.name AS away_team, m.starts_at
     FROM matches m
     JOIN teams ht ON ht.id = m.home_team_id
     JOIN teams at ON at.id = m.away_team_id
     JOIN subscriptions s ON s.team_id IN (m.home_team_id, m.away_team_id)
     WHERE s.user_id = $1 AND m.status IN ('scheduled', 'live')
     ORDER BY m.starts_at`,
    [userId],
  );
  return rows;
}
