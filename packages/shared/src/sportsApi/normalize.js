// Translate raw sports-API payloads into the internal shapes the rest of the
// app depends on. Keeping this in one place means a provider swap touches only
// this file (and client.js), not the workers or API.

// API-Football fixture status short codes -> our coarse status.
const LIVE_CODES = new Set(['1H', '2H', 'HT', 'ET', 'BT', 'P', 'LIVE']);
const FINISHED_CODES = new Set(['FT', 'AET', 'PEN']);

export function normalizeStatus(shortCode) {
  if (FINISHED_CODES.has(shortCode)) return 'finished';
  if (LIVE_CODES.has(shortCode)) return 'live';
  return 'scheduled';
}

// raw team object -> { externalId, name, league }
export function normalizeTeam(raw) {
  return {
    externalId: String(raw.team?.id ?? raw.id),
    name: raw.team?.name ?? raw.name,
    league: raw.league?.name ?? null,
  };
}

export function normalizeTeamSearch(apiResponse) {
  return (apiResponse.response ?? []).map(normalizeTeam);
}

// raw fixture -> internal match state used by the poller's diff.
export function normalizeFixture(raw) {
  return {
    externalId: String(raw.fixture.id),
    status: normalizeStatus(raw.fixture.status?.short),
    minute: raw.fixture.status?.elapsed ?? null,
    homeExternalId: String(raw.teams.home.id),
    awayExternalId: String(raw.teams.away.id),
    homeScore: raw.goals.home ?? 0,
    awayScore: raw.goals.away ?? 0,
    // events (cards, goals with scorer) if the provider includes them
    events: (raw.events ?? []).map((e) => ({
      type: e.type, // 'Goal' | 'Card' | ...
      detail: e.detail, // 'Yellow Card' | 'Red Card' | 'Normal Goal' ...
      minute: e.time?.elapsed ?? null,
      teamExternalId: String(e.team?.id),
      player: e.player?.name ?? null,
      playerExternalId: e.player?.id != null ? String(e.player.id) : null,
    })),
  };
}

export function normalizeFixtures(apiResponse) {
  return (apiResponse.response ?? []).map(normalizeFixture);
}
