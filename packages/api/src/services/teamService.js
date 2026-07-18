import { teamsRepo, sportsApi, normalize, logger } from '@sports/shared';

// Search-then-cache: try the local cache first; if thin, hit the sports API
// and upsert results into the teams table so subsequent searches are cheap.
export async function searchTeams(q) {
  const cached = await teamsRepo.searchTeamsCached(q);
  if (cached.length >= 5) return cached;

  try {
    const apiResponse = await sportsApi.searchTeams(q);
    const normalized = normalize.normalizeTeamSearch(apiResponse);
    const upserted = await Promise.all(
      normalized.map((t) => teamsRepo.upsertTeam(t)),
    );
    // Merge cache + fresh, de-duplicated by id.
    const byId = new Map();
    for (const t of [...cached, ...upserted]) byId.set(t.id, t);
    return [...byId.values()];
  } catch (err) {
    logger.warn({ err, q }, 'team search fell back to cache');
    return cached; // degrade gracefully if upstream is down
  }
}
