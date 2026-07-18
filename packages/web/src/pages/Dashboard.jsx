import { useEffect, useState } from 'react';
import { api } from '../lib/api.js';
import { enablePushNotifications } from '../registerSW.js';
import MatchTile from '../components/MatchTile.jsx';

export default function Dashboard() {
  const [matches, setMatches] = useState([]);
  const [error, setError] = useState(null);
  const [pushMsg, setPushMsg] = useState(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .liveMatches()
        .then((d) => alive && setMatches(d.matches))
        .catch((e) => alive && setError(e.message));
    load();
    const id = setInterval(load, 20_000); // refresh scores periodically
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  async function onEnablePush() {
    try {
      await enablePushNotifications();
      setPushMsg('Push notifications enabled ✅');
    } catch (err) {
      setPushMsg(err.message);
    }
  }

  return (
    <section>
      <div className="row-between">
        <h1>Live matches</h1>
        <button onClick={onEnablePush}>Enable notifications</button>
      </div>
      {pushMsg && <p className="muted">{pushMsg}</p>}
      {error && <p className="error">{error}</p>}
      {matches.length === 0 ? (
        <p className="muted">No live matches for your followed teams right now.</p>
      ) : (
        <div className="grid">
          {matches.map((m) => <MatchTile key={m.id} match={m} />)}
        </div>
      )}
    </section>
  );
}
