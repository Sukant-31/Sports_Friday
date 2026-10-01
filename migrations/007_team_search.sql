-- Indexed substring and fuzzy candidates without scanning the full team catalogue.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_teams_name_trgm ON teams USING gin (lower(name) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_teams_name_prefix ON teams (lower(name) text_pattern_ops);
