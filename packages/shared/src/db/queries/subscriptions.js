import { query } from '../pool.js';

export async function listSubscriptions(userId) {
  const { rows } = await query(
    `SELECT s.id, s.team_id, t.name AS team_name, t.league,
            s.notify_goals, s.notify_cards, s.notify_match_status, s.created_at
     FROM subscriptions s
     JOIN teams t ON t.id = s.team_id
     WHERE s.user_id = $1
     ORDER BY t.name`,
    [userId],
  );
  return rows;
}

export async function createSubscription({
  userId,
  teamId,
  notifyGoals = true,
  notifyCards = false,
  notifyMatchStatus = true,
}) {
  const { rows } = await query(
    `INSERT INTO subscriptions
       (user_id, team_id, notify_goals, notify_cards, notify_match_status)
     VALUES ($1, $2, $3, $4, $5)
     ON CONFLICT (user_id, team_id) DO UPDATE
       SET notify_goals = EXCLUDED.notify_goals,
           notify_cards = EXCLUDED.notify_cards,
           notify_match_status = EXCLUDED.notify_match_status
     RETURNING *`,
    [userId, teamId, notifyGoals, notifyCards, notifyMatchStatus],
  );
  return rows[0];
}

export async function updateSubscription(userId, id, prefs) {
  const { rows } = await query(
    `UPDATE subscriptions
     SET notify_goals = COALESCE($3, notify_goals),
         notify_cards = COALESCE($4, notify_cards),
         notify_match_status = COALESCE($5, notify_match_status)
     WHERE id = $1 AND user_id = $2
     RETURNING *`,
    [id, userId, prefs.notifyGoals, prefs.notifyCards, prefs.notifyMatchStatus],
  );
  return rows[0] ?? null;
}

export async function deleteSubscription(userId, id) {
  const { rowCount } = await query(
    `DELETE FROM subscriptions WHERE id = $1 AND user_id = $2`,
    [id, userId],
  );
  return rowCount > 0;
}

// Push targets for a team event, filtered by the users' notify_* preferences.
// `eventType` is one of: goal | card | kickoff | full_time.
export async function findPushTargetsForEvent(teamId, eventType) {
  const prefColumn =
    eventType === 'goal'
      ? 's.notify_goals'
      : eventType === 'card'
        ? 's.notify_cards'
        : 's.notify_match_status'; // kickoff | full_time

  const { rows } = await query(
    `SELECT ps.id AS push_id, ps.endpoint, ps.p256dh, ps.auth, s.user_id
     FROM subscriptions s
     JOIN push_subscriptions ps ON ps.user_id = s.user_id
     WHERE s.team_id = $1 AND ${prefColumn} = true`,
    [teamId],
  );
  return rows;
}
