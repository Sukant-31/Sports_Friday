-- Accepted receipts remain separate from terminal non-acceptance outcomes.
CREATE TABLE IF NOT EXISTS notification_terminal_outcomes (
  event_id UUID REFERENCES match_events(id) ON DELETE CASCADE,
  push_id UUID REFERENCES push_subscriptions(id) ON DELETE CASCADE,
  outcome TEXT NOT NULL CHECK (outcome IN ('permanent_failure', 'simulated')),
  status_code SMALLINT,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (event_id, push_id)
);
