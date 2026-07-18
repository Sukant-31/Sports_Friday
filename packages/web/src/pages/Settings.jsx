import { useEffect, useState } from 'react';
import { api } from '../lib/api.js';
import NotificationToggle from '../components/NotificationToggle.jsx';

export default function Settings() {
  const [subs, setSubs] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listSubscriptions()
      .then((d) => setSubs(d.subscriptions))
      .catch((e) => setError(e.message));
  }, []);

  // DB rows use snake_case (notify_goals); the API PATCH body expects camelCase.
  const API_FIELD = {
    notify_goals: 'notifyGoals',
    notify_cards: 'notifyCards',
    notify_match_status: 'notifyMatchStatus',
  };

  async function toggle(sub, field) {
    const value = !sub[field];
    // optimistic update (snake_case for local display state)
    setSubs((prev) => prev.map((s) => (s.id === sub.id ? { ...s, [field]: value } : s)));
    try {
      await api.updateSubscription(sub.id, { [API_FIELD[field]]: value });
    } catch (err) {
      setError(err.message);
    }
  }

  async function unfollow(sub) {
    setSubs((prev) => prev.filter((s) => s.id !== sub.id));
    try {
      await api.unsubscribe(sub.id);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <section>
      <h1>Notification settings</h1>
      {error && <p className="error">{error}</p>}
      {subs.length === 0 ? (
        <p className="muted">You're not following any teams yet.</p>
      ) : (
        <ul className="list">
          {subs.map((s) => (
            <li key={s.id} className="card">
              <div className="row-between">
                <strong>{s.team_name}</strong>
                <button className="link" onClick={() => unfollow(s)}>Unfollow</button>
              </div>
              <div className="toggles">
                <NotificationToggle label="Goals" on={s.notify_goals} onToggle={() => toggle(s, 'notify_goals')} />
                <NotificationToggle label="Cards" on={s.notify_cards} onToggle={() => toggle(s, 'notify_cards')} />
                <NotificationToggle label="Kickoff / full-time" on={s.notify_match_status} onToggle={() => toggle(s, 'notify_match_status')} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
