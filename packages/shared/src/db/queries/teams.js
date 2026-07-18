import { query } from '../pool.js';

// Insert-or-return a team by its opaque external id (from the sports API).
export async function upsertTeam({ externalId, name, league }) {
  const { rows } = await query(
    `INSERT INTO teams (external_id, name, league)
     VALUES ($1, $2, $3)
     ON CONFLICT (external_id) DO UPDATE
       SET name = EXCLUDED.name, league = EXCLUDED.league
     RETURNING id, external_id, name, league`,
    [externalId, name, league ?? null],
  );
  return rows[0];
}

export async function searchTeamsCached(q) {
  const { rows } = await query(
    `SELECT id, external_id, name, league
     FROM teams
     WHERE name ILIKE $1
     ORDER BY name
     LIMIT 20`,
    [`%${q}%`],
  );
  return rows;
}

export async function findTeamById(id) {
  const { rows } = await query(
    `SELECT id, external_id, name, league FROM teams WHERE id = $1`,
    [id],
  );
  return rows[0] ?? null;
}
