// Optional: seed a few teams so local dev has something to follow without
// hitting the sports API. Idempotent (upsert by external_id).
import pg from 'pg';

const { Pool } = pg;
const pool = new Pool({ connectionString: process.env.DATABASE_URL });

const TEAMS = [
  { externalId: '42', name: 'Arsenal', league: 'Premier League' },
  { externalId: '50', name: 'Manchester City', league: 'Premier League' },
  { externalId: '40', name: 'Liverpool', league: 'Premier League' },
  { externalId: '541', name: 'Real Madrid', league: 'La Liga' },
  { externalId: '529', name: 'Barcelona', league: 'La Liga' },
];

async function run() {
  for (const t of TEAMS) {
    await pool.query(
      `INSERT INTO teams (external_id, name, league)
       VALUES ($1, $2, $3)
       ON CONFLICT (external_id) DO UPDATE SET name = EXCLUDED.name, league = EXCLUDED.league`,
      [t.externalId, t.name, t.league],
    );
  }
  console.log(`seeded ${TEAMS.length} teams`);
  await pool.end();
}

run();
