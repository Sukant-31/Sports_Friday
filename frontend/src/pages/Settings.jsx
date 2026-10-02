import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api.js';
import NotificationToggle from '../components/NotificationToggle.jsx';

export default function Settings() {
  const [subs, setSubs] = useState([]);
  const [error, setError] = useState(null);
  const saving = useRef(new Set());
  const [pending, setPending] = useState(new Set());

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

  async function save(id, change) {
    if (saving.current.has(id)) return;
    saving.current.add(id);
    setPending(new Set(saving.current));
    setError(null);
    try {
      await change();
    } catch (err) {
      setError(err.message);
    } finally {
      saving.current.delete(id);
      setPending(new Set(saving.current));
    }
  }

  function toggle(sub, field) {
    const value = !sub[field];
    return save(sub.id, async () => {
      await api.updateSubscription(sub.id, { [API_FIELD[field]]: value });
      setSubs((prev) => prev.map((s) => (s.id === sub.id ? { ...s, [field]: value } : s)));
    });
  }

  function unfollow(sub) {
    return save(sub.id, async () => {
      await api.unsubscribe(sub.id);
      setSubs((prev) => prev.filter((s) => s.id !== sub.id));
    });
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
            <li key={s.id} className="card" aria-busy={pending.has(s.id)}>
              <div className="row-between">
                <strong>{s.team_name}</strong>
                <button className="link" disabled={pending.has(s.id)} onClick={() => unfollow(s)}>Unfollow</button>
              </div>
              <div className="toggles">
                <NotificationToggle label="Goals" on={s.notify_goals} disabled={pending.has(s.id)} onToggle={() => toggle(s, 'notify_goals')} />
                <NotificationToggle label="Cards" on={s.notify_cards} disabled={pending.has(s.id)} onToggle={() => toggle(s, 'notify_cards')} />
                <NotificationToggle label="Kickoff / full-time" on={s.notify_match_status} disabled={pending.has(s.id)} onToggle={() => toggle(s, 'notify_match_status')} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
