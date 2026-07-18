import { subscriptionsRepo, teamsRepo } from '@sports/shared';
import { httpError } from '../middleware/errorHandler.js';

export function list(userId) {
  return subscriptionsRepo.listSubscriptions(userId);
}

export async function create(userId, input) {
  const team = await teamsRepo.findTeamById(input.teamId);
  if (!team) throw httpError(404, 'Team not found');
  return subscriptionsRepo.createSubscription({ userId, ...input });
}

export async function update(userId, id, prefs) {
  const updated = await subscriptionsRepo.updateSubscription(userId, id, prefs);
  if (!updated) throw httpError(404, 'Subscription not found');
  return updated;
}

export async function remove(userId, id) {
  const ok = await subscriptionsRepo.deleteSubscription(userId, id);
  if (!ok) throw httpError(404, 'Subscription not found');
}
