import { Queue } from 'bullmq';
import { redis } from '../redis/client.js';

export const MATCH_EVENTS_QUEUE = 'match-events';

// Shared queue handle for producers (poller) and for constructing the Worker
// (notifier). The Worker itself is created in the workers package.
export const matchEventsQueue = new Queue(MATCH_EVENTS_QUEUE, {
  connection: redis,
  defaultJobOptions: {
    attempts: 5,
    backoff: { type: 'exponential', delay: 2_000 },
    removeOnComplete: 1_000,
    removeOnFail: 5_000,
  },
});

// payload: { matchEventId, matchId, teamId, type, detail }
export function enqueueMatchEvent(payload) {
  return matchEventsQueue.add('event', payload);
}
