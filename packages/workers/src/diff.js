// Pure event-detection: given the previous known state of a match and the
// freshly-fetched state, return the list of events that occurred in between.
// No I/O, no dedup ledger — that gating happens in the poller. This function's
// only job is "what changed", and it must be deterministic for the tests.
//
// prev/next shape (from normalize.normalizeFixture, trimmed):
//   { status, minute, homeScore, awayScore,
//     homeExternalId, awayExternalId, events: [...] }
//
// Returns events: [{ type, teamExternalId, minute, detail, homeScore,
//                    awayScore, player?, playerExternalId? }]

export function diffMatch(prev, next) {
  const events = [];

  // First time we see a match (no prev): only emit lifecycle events implied by
  // the current status, never phantom goals for the score it's already at.
  const before = prev ?? {
    status: 'scheduled',
    homeScore: 0,
    awayScore: 0,
  };

  // --- status transitions ---
  if (before.status !== 'live' && next.status === 'live') {
    events.push(lifecycle('kickoff', next));
  }
  if (before.status !== 'finished' && next.status === 'finished') {
    events.push(lifecycle('full_time', next));
  }

  // --- goals ---
  // Prefer explicit goal events from the API (carry scorer + team); fall back
  // to score deltas when the provider gives only aggregate goals.
  const apiGoals = (next.events ?? []).filter((e) => e.type === 'Goal');
  if (apiGoals.length && prev) {
    const prevGoalCount = (prev.events ?? []).filter((e) => e.type === 'Goal').length;
    for (const g of apiGoals.slice(prevGoalCount)) {
      events.push({
        type: 'goal',
        teamExternalId: g.teamExternalId,
        minute: g.minute,
        detail: g.detail ?? 'Goal',
        player: g.player,
        playerExternalId: g.playerExternalId,
        homeScore: next.homeScore,
        awayScore: next.awayScore,
      });
    }
  } else {
    if (next.homeScore > before.homeScore) {
      events.push(goalFromScore(next.homeExternalId, next));
    }
    if (next.awayScore > before.awayScore) {
      events.push(goalFromScore(next.awayExternalId, next));
    }
  }

  // --- cards ---
  const apiCards = (next.events ?? []).filter((e) => e.type === 'Card');
  if (apiCards.length && prev) {
    const prevCardCount = (prev.events ?? []).filter((e) => e.type === 'Card').length;
    for (const c of apiCards.slice(prevCardCount)) {
      events.push({
        type: 'card',
        teamExternalId: c.teamExternalId,
        minute: c.minute,
        detail: c.detail ?? 'Card',
        player: c.player,
        playerExternalId: c.playerExternalId,
      });
    }
  }

  return events;
}

function lifecycle(type, next) {
  return {
    type,
    // Lifecycle events aren't team-specific; the poller fans out to both teams.
    teamExternalId: null,
    minute: next.minute ?? null,
    detail: type === 'kickoff' ? 'Kick-off' : 'Full-time',
    homeScore: next.homeScore,
    awayScore: next.awayScore,
  };
}

function goalFromScore(teamExternalId, next) {
  return {
    type: 'goal',
    teamExternalId,
    minute: next.minute ?? null,
    detail: 'Goal',
    homeScore: next.homeScore,
    awayScore: next.awayScore,
  };
}
