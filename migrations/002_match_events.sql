-- Idempotency ledger: one row per event the poller has ever emitted.
-- The poller inserts here (ON CONFLICT DO NOTHING) before enqueueing; a
-- conflict means the event was already seen and must NOT be re-notified.
CREATE TABLE match_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID REFERENCES matches(id) ON DELETE CASCADE,
  team_id UUID REFERENCES teams(id),
  type TEXT NOT NULL,                 -- goal | card | kickoff | full_time
  detail JSONB DEFAULT '{}',
  dedup_key TEXT UNIQUE NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
