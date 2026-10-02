-- Rolling request budget and persistent response cache. No existing data removed.
CREATE TABLE IF NOT EXISTS sports_api_requests (
  id BIGSERIAL PRIMARY KEY,
  scope TEXT NOT NULL,
  requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  units INTEGER NOT NULL DEFAULT 1 CHECK (units > 0),
  kind TEXT NOT NULL DEFAULT 'request' CHECK (kind IN ('request', 'provider_baseline'))
);
CREATE INDEX IF NOT EXISTS sports_api_requests_scope_time_idx
  ON sports_api_requests (scope, requested_at);
CREATE TABLE IF NOT EXISTS sports_api_cache (
  scope TEXT NOT NULL,
  cache_key TEXT NOT NULL,
  payload JSONB NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  expires_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (scope, cache_key)
);
ALTER TABLE teams ADD COLUMN IF NOT EXISTS last_discovered_at TIMESTAMPTZ;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ;
