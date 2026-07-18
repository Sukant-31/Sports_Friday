-- Hot-path indexes.
CREATE INDEX idx_subscriptions_team ON subscriptions(team_id);
CREATE INDEX idx_subscriptions_user ON subscriptions(user_id);
CREATE INDEX idx_matches_status     ON matches(status);
CREATE INDEX idx_push_subs_user     ON push_subscriptions(user_id);
CREATE INDEX idx_match_events_match ON match_events(match_id);
