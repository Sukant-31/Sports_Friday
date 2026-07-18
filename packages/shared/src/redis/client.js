import { Redis } from 'ioredis';
import { config } from '../config.js';

// BullMQ requires maxRetriesPerRequest: null on its connection.
export const redis = new Redis(config.redisUrl, {
  maxRetriesPerRequest: null,
});

const STATE_TTL_SECONDS = 60 * 60 * 3; // 3h — matches don't run longer

function stateKey(matchId) {
  return `match:${matchId}:state`;
}

export async function getMatchState(matchId) {
  const raw = await redis.get(stateKey(matchId));
  return raw ? JSON.parse(raw) : null;
}

export async function setMatchState(matchId, state) {
  await redis.set(stateKey(matchId), JSON.stringify(state), 'EX', STATE_TTL_SECONDS);
}

export async function closeRedis() {
  await redis.quit();
}
