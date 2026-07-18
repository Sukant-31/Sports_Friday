-- Per-match mute: a user can silence notifications for one match without
-- unfollowing the team. The notifier excludes muted users when resolving
-- push targets.
CREATE TABLE muted_matches (
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  match_id UUID REFERENCES matches(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (user_id, match_id)
);
