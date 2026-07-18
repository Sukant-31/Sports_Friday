import {
  config,
  logger,
  sportsApi,
  normalize,
  getMatchState,
  setMatchState,
  matchesRepo,
  teamsRepo,
  matchEventsRepo,
  enqueueMatchEvent,
} from '@sports/shared';
import { diffMatch } from './diff.js';
import { dedupKey } from './dedupKey.js';

const log = logger.child({ worker: 'poller' });

// Poll one match: fetch fresh state, diff against the last-known state, gate
// each event through the idempotency ledger, and enqueue the fresh ones.
async function pollMatch(match) {
  const raw = await sportsApi.getLiveFixture(match.external_id);
  const [fixture] = normalize.normalizeFixtures(raw);
  if (!fixture) return;

  // Baseline: Redis cache, else rehydrate from the persisted match row so a
  // cache miss doesn't replay history as new events.
  const prev =
    (await getMatchState(match.id)) ??
    {
      status: match.status,
      homeScore: match.home_score,
      awayScore: match.away_score,
      homeExternalId: fixture.homeExternalId,
      awayExternalId: fixture.awayExternalId,
      events: [],
    };

  const events = diffMatch(prev, fixture);

  for (const event of events) {
    // Resolve the team the alert is about. Lifecycle events (no team) fan out
    // to both sides.
    const teamExternalIds = event.teamExternalId
      ? [event.teamExternalId]
      : [fixture.homeExternalId, fixture.awayExternalId];

    for (const extId of teamExternalIds) {
      const team = await teamsRepo.upsertTeam({
        externalId: extId,
        name: extId, // real name backfilled by team search; placeholder is fine
      });

      const key = dedupKey(fixture.externalId, event) + `:t${extId}`;
      const recorded = await matchEventsRepo.recordEventIfNew({
        matchId: match.id,
        teamId: team.id,
        type: event.type,
        detail: event,
        dedupKey: key,
      });
      if (!recorded) continue; // already handled — no duplicate notification

      await enqueueMatchEvent({
        matchEventId: recorded.id,
        matchId: match.id,
        teamId: team.id,
        type: event.type,
        detail: event,
      });
      log.info({ matchId: match.id, type: event.type, team: extId }, 'event enqueued');
    }
  }

  // Persist latest state to both Redis (fast path) and Postgres (source of truth).
  const nextState = {
    status: fixture.status,
    homeScore: fixture.homeScore,
    awayScore: fixture.awayScore,
    homeExternalId: fixture.homeExternalId,
    awayExternalId: fixture.awayExternalId,
    events: fixture.events,
  };
  await setMatchState(match.id, nextState);
  await matchesRepo.updateMatchState(match.id, {
    status: fixture.status,
    homeScore: fixture.homeScore,
    awayScore: fixture.awayScore,
  });
}

export async function tick() {
  const matches = await matchesRepo.findPollableMatches();
  if (matches.length === 0) {
    log.debug('no subscribed matches to poll');
    return;
  }
  log.debug({ count: matches.length }, 'polling matches');
  for (const match of matches) {
    try {
      await pollMatch(match);
    } catch (err) {
      log.warn({ err, matchId: match.id }, 'poll failed for match');
    }
  }
}

export function startPoller() {
  log.info({ intervalMs: config.pollIntervalMs }, 'poller started');
  // Guard against overlapping ticks if a cycle runs long.
  let running = false;
  const run = async () => {
    if (running) return;
    running = true;
    try {
      await tick();
    } finally {
      running = false;
    }
  };
  run();
  return setInterval(run, config.pollIntervalMs);
}

// Allow running standalone: `pnpm --filter @sports/workers poller`
if (import.meta.url === `file://${process.argv[1]}`) {
  startPoller();
}
