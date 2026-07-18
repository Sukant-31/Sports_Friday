import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../lib/api.js';
import { enablePushNotifications } from '../registerSW.js';
import MatchTile from '../components/MatchTile.jsx';

const POLL_MS = 15_000;

function relTime(ts) {
  if (!ts) return '';
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s ago`;
  return `${Math.round(s / 60)}m ago`;
}

export default function Dashboard() {
  const [matches, setMatches] = useState(null); // null = initial loading
  const [error, setError] = useState(null);
  const [pushMsg, setPushMsg] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [, forceTick] = useState(0); // re-render the "updated Xs ago" label
  const timer = useRef(null);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const d = await api.liveMatches();
      setMatches(d.matches);
      setUpdatedAt(Date.now());
      setError(null);
    } catch (e) {
      setError(e.message); // keep showing last-known data
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const start = () => {
      stop();
      timer.current = setInterval(load, POLL_MS);
    };
    const stop = () => timer.current && clearInterval(timer.current);
    // Pause polling when the tab is hidden; refresh immediately when it returns.
    const onVisibility = () => {
      if (document.hidden) stop();
      else {
        load();
        start();
      }
    };

    load();
    start();
    document.addEventListener('visibilitychange', onVisibility);
    const relTimer = setInterval(() => forceTick((t) => t + 1), 1000);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
      clearInterval(relTimer);
    };
  }, [load]);

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
      <div className="dash-head">
        <div>
          <h1>Live matches</h1>
          <p className="updated">
            {updatedAt ? `Updated ${relTime(updatedAt)}` : 'Loading…'}
            {refreshing && <span className="spinner" aria-label="refreshing" />}
          </p>
        </div>
        <div className="dash-actions">
          <button className="ghost" onClick={load} disabled={refreshing}>
            Refresh
          </button>
          <button onClick={onEnablePush}>Enable notifications</button>
        </div>
      </div>

      {pushMsg && <p className="muted">{pushMsg}</p>}
      {error && (
        <p className="error">
          {error} — <button className="link" onClick={load}>retry</button>
        </p>
      )}

      {matches === null ? (
        <div className="grid">
          {[0, 1, 2].map((i) => (
            <div key={i} className="card skeleton" />
          ))}
        </div>
      ) : matches.length === 0 ? (
        <div className="card empty">
          <p>No live matches for your followed teams right now.</p>
          <p className="muted">
            Follow more teams from <strong>Find teams</strong>, and their live
            matches will appear here automatically.
          </p>
        </div>
      ) : (
        <div className="grid">
          {matches.map((m) => (
            <MatchTile key={m.id} match={m} />
          ))}
        </div>
      )}
    </section>
  );
}
