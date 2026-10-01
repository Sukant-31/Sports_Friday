import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api.js';
import { useAuth } from '../lib/auth.jsx';
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
  const { user, loading: authLoading } = useAuth();
  const [matches, setMatches] = useState(null); // null = initial loading
  const [error, setError] = useState(null);
  const [warning, setWarning] = useState(null);
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
      setWarning(d.warning);
      setUpdatedAt(Date.now());
      setError(null);
    } catch (e) {
      setError(e.message); // keep showing last-known data
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (!user) return;
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
  }, [load, user]);

  async function onEnablePush() {
    try {
      await enablePushNotifications();
      setPushMsg('Push notifications enabled ✅');
    } catch (err) {
      setPushMsg(err.message);
    }
  }

  if (authLoading) {
    return (
      <section>
        <h1>Your matches</h1>
        <div className="grid">
          {[0, 1, 2].map((i) => (
            <div key={i} className="card skeleton" />
          ))}
        </div>
      </section>
    );
  }

  if (!user) {
    return (
      <section>
        <h1>Your matches</h1>
        <div className="card empty">
          <p>Log in to see matches for the teams you follow.</p>
          <p className="muted">
            <Link to="/login">Log in</Link> or <Link to="/signup">create an account</Link>.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section>
      <div className="dash-head">
        <div>
          <h1>Your matches</h1>
          <p className="muted">Live, upcoming, and finished matches from the last 24 hours.</p>
          <p className="updated">
            {updatedAt ? `Checked ${relTime(updatedAt)}` : 'Loading…'}
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
      {warning && <p role="status" className="muted">{warning}</p>}
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
          <p>No upcoming, live, or recently finished matches for your followed teams.</p>
          <p className="muted">
            Follow more teams from <strong>Find teams</strong>, and their
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
