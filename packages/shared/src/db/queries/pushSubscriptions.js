import { query } from '../pool.js';

export async function upsertPushSubscription({ userId, endpoint, p256dh, auth }) {
  const { rows } = await query(
    `INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
     VALUES ($1, $2, $3, $4)
     ON CONFLICT (user_id, endpoint) DO UPDATE
       SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth
     RETURNING id, endpoint`,
    [userId, endpoint, p256dh, auth],
  );
  return rows[0];
}

export async function deletePushSubscriptionByEndpoint(userId, endpoint) {
  const { rowCount } = await query(
    `DELETE FROM push_subscriptions WHERE user_id = $1 AND endpoint = $2`,
    [userId, endpoint],
  );
  return rowCount > 0;
}

// Called by the notifier when Web Push returns 404/410 (expired subscription).
export async function deletePushSubscriptionById(id) {
  await query(`DELETE FROM push_subscriptions WHERE id = $1`, [id]);
}
