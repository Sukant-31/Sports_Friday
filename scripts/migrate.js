// Minimal forward-only migration runner. Applies every migrations/*.sql that
// hasn't been recorded in the schema_migrations table, in filename order, each
// in its own transaction. No down migrations by design (§ architecture doc).
import { readdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import pg from 'pg';

const { Pool } = pg;
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const migrationsDir = path.join(__dirname, '..', 'migrations');

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) {
  console.error('DATABASE_URL is not set (source your .env or pass it inline)');
  process.exit(1);
}

const pool = new Pool({ connectionString: databaseUrl });

async function run() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      name TEXT PRIMARY KEY,
      applied_at TIMESTAMPTZ DEFAULT now()
    )
  `);

  const applied = new Set(
    (await pool.query('SELECT name FROM schema_migrations')).rows.map((r) => r.name),
  );

  const files = (await readdir(migrationsDir))
    .filter((f) => f.endsWith('.sql'))
    .sort();

  let count = 0;
  for (const file of files) {
    if (applied.has(file)) continue;
    const sql = await readFile(path.join(migrationsDir, file), 'utf8');
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      await client.query(sql);
      await client.query('INSERT INTO schema_migrations (name) VALUES ($1)', [file]);
      await client.query('COMMIT');
      console.log(`applied ${file}`);
      count += 1;
    } catch (err) {
      await client.query('ROLLBACK');
      console.error(`failed on ${file}:`, err.message);
      process.exit(1);
    } finally {
      client.release();
    }
  }

  console.log(count === 0 ? 'already up to date' : `applied ${count} migration(s)`);
  await pool.end();
}

run();
