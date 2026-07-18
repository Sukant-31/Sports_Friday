// Deterministic identity for an event, so the same real-world occurrence
// always maps to the same key regardless of when/how often it's observed.
// Pure function — no I/O — which keeps it trivially unit-testable.

export function dedupKey(externalMatchId, event) {
  switch (event.type) {
    case 'goal':
      // A goal is identified by the match + scoring team + the resulting
      // aggregate score. If the API gives a scorer, prefer that (handles two
      // goals in the same minute, own goals, etc.).
      return event.playerExternalId
        ? `${externalMatchId}:goal:${event.teamExternalId}:${event.playerExternalId}:${event.minute ?? 'na'}`
        : `${externalMatchId}:goal:${event.homeScore}-${event.awayScore}`;
    case 'card':
      return `${externalMatchId}:card:${event.teamExternalId}:${event.playerExternalId ?? event.minute}:${event.detail ?? ''}`;
    case 'kickoff':
      return `${externalMatchId}:kickoff`;
    case 'full_time':
      return `${externalMatchId}:full_time`;
    default:
      return `${externalMatchId}:${event.type}:${event.minute ?? 'na'}`;
  }
}
