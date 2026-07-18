import { query } from '../pool.js';

export async function createUser({ email, passwordHash }) {
  const { rows } = await query(
    `INSERT INTO users (email, password_hash)
     VALUES ($1, $2)
     RETURNING id, email, created_at`,
    [email, passwordHash],
  );
  return rows[0];
}

export async function findUserByEmail(email) {
  const { rows } = await query(
    `SELECT id, email, password_hash, created_at FROM users WHERE email = $1`,
    [email],
  );
  return rows[0] ?? null;
}

export async function findUserById(id) {
  const { rows } = await query(
    `SELECT id, email, created_at FROM users WHERE id = $1`,
    [id],
  );
  return rows[0] ?? null;
}
