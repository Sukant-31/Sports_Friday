// Barrel export for the shared package.
export { config, isProd } from './config.js';
export { logger, childLogger } from './logger.js';
export { pool, query, withTransaction, closePool } from './db/pool.js';
export {
  redis,
  getMatchState,
  setMatchState,
  closeRedis,
} from './redis/client.js';
export {
  matchEventsQueue,
  enqueueMatchEvent,
  MATCH_EVENTS_QUEUE,
} from './queue/matchEvents.js';
export * as sportsApi from './sportsApi/client.js';
export * as normalize from './sportsApi/normalize.js';

export * as usersRepo from './db/queries/users.js';
export * as teamsRepo from './db/queries/teams.js';
export * as subscriptionsRepo from './db/queries/subscriptions.js';
export * as matchesRepo from './db/queries/matches.js';
export * as matchEventsRepo from './db/queries/matchEvents.js';
export * as pushSubscriptionsRepo from './db/queries/pushSubscriptions.js';
