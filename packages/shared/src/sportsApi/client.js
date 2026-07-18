import { config } from '../config.js';
import { logger } from '../logger.js';

// Minimal HTTP client for the sports data API with:
//  - auth header injection
//  - exponential backoff on 429/5xx
//  - a tiny circuit breaker so a sustained outage stops hammering the API
const MAX_RETRIES = 3;
const BREAKER_THRESHOLD = 5; // consecutive failures before opening
const BREAKER_COOLDOWN_MS = 30_000;

let consecutiveFailures = 0;
let breakerOpenUntil = 0;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class SportsApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'SportsApiError';
    this.status = status;
  }
}

async function request(path, { params } = {}) {
  if (Date.now() < breakerOpenUntil) {
    throw new SportsApiError('Circuit breaker open', 503);
  }

  const url = new URL(path, config.sportsApi.baseUrl);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
  }

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt += 1) {
    try {
      const res = await fetch(url, {
        headers: {
          // API-Football style; adjust header name if using another provider.
          'x-apisports-key': config.sportsApi.key,
          accept: 'application/json',
        },
      });

      if (res.status === 429 || res.status >= 500) {
        throw new SportsApiError(`Upstream ${res.status}`, res.status);
      }
      if (!res.ok) {
        throw new SportsApiError(`Request failed ${res.status}`, res.status);
      }

      consecutiveFailures = 0;
      return res.json();
    } catch (err) {
      const retriable = err instanceof SportsApiError && (err.status === 429 || err.status >= 500);
      if (!retriable || attempt === MAX_RETRIES) {
        consecutiveFailures += 1;
        if (consecutiveFailures >= BREAKER_THRESHOLD) {
          breakerOpenUntil = Date.now() + BREAKER_COOLDOWN_MS;
          logger.warn({ cooldownMs: BREAKER_COOLDOWN_MS }, 'sports API circuit breaker opened');
        }
        throw err;
      }
      const delay = 2 ** attempt * 500; // 500ms, 1s, 2s
      logger.debug({ attempt, delay, path }, 'retrying sports API request');
      await sleep(delay);
    }
  }
  // unreachable
  throw new SportsApiError('exhausted retries', 500);
}

// --- Endpoint wrappers (API-Football shapes; see normalize.js) ---

export function searchTeams(q) {
  return request('/teams', { params: { search: q } });
}

export function getLiveFixture(externalId) {
  return request('/fixtures', { params: { id: externalId } });
}

export function getLiveFixtures() {
  return request('/fixtures', { params: { live: 'all' } });
}
