-- Existing events are historical: do not replay them on upgrade.
ALTER TABLE match_events ADD COLUMN delivered_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE match_events ALTER COLUMN delivered_at DROP DEFAULT;
CREATE INDEX match_events_pending_idx ON match_events (created_at)
  WHERE delivered_at IS NULL;
CREATE TABLE notification_deliveries (
  event_id UUID REFERENCES match_events(id) ON DELETE CASCADE,
  push_id UUID REFERENCES push_subscriptions(id) ON DELETE CASCADE,
  PRIMARY KEY (event_id, push_id)
);
ALTER TABLE subscriptions ALTER COLUMN notify_cards SET DEFAULT true;
-- Preserve existing users' explicit notification preferences.
