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
  const [filter, setFilter] = useState('all');
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

  const counts = {
    live: matches?.filter((m) => m.status === 'live').length ?? 0,
    scheduled: matches?.filter((m) => m.status === 'scheduled').length ?? 0,
    finished: matches?.filter((m) => m.status === 'finished').length ?? 0,
  };
  const visibleMatches = matches?.filter((m) => filter === 'all' || m.status === filter);

  if (authLoading) {
    return (
      <section className="dashboard">
        <p className="eyebrow">YOUR PERSONAL MATCHDAY</p>
          <h1>Your matches<span className="heading-dot">.</span></h1>
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
      <section className="dashboard">
        <p className="eyebrow">YOUR PERSONAL MATCHDAY</p>
          <h1>Your matches<span className="heading-dot">.</span></h1>
        <div className="card empty">
          <div className="empty-icon" aria-hidden="true">⚽</div>
          <h2>Your matchday starts here</h2>
          <p>Log in to see matches for the teams you follow.</p>
          <p className="muted">
            <Link to="/login">Log in</Link> or <Link to="/signup">create an account</Link>.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="dashboard">
      <div className="dash-head">
        <div>
          <p className="eyebrow">YOUR PERSONAL MATCHDAY</p>
          <h1>Your matches<span className="heading-dot">.</span></h1>
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

      <div className="match-summary" aria-label="Match overview">
        {[['live', 'Live now', 'Happening on the pitch'], ['scheduled', 'Upcoming', 'Next up for your teams'], ['finished', 'Full time', 'The latest results']].map(([status, label, detail]) => (
          <div className={`summary-card ${status}`} key={status}>
            <span className="summary-label">{status === 'live' && <span className="summary-dot" />} {label}</span>
            <strong>{matches === null ? '—' : counts[status]}</strong>
            <span className="muted">{detail}</span>
          </div>
        ))}
      </div>
      <div className="match-toolbar">
        <div><h2>Match centre</h2><p className="muted">All the action from the teams you follow.</p></div>
        <Link className="text-link" to="/search">Find teams <span aria-hidden="true">↗</span></Link>
      </div>
      <div className="match-filters" role="group" aria-label="Filter matches">
        {[['all', 'All matches'], ['live', 'Live'], ['scheduled', 'Upcoming'], ['finished', 'Finished']].map(([value, label]) => (
          <button key={value} className={filter === value ? 'selected' : ''} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>
        ))}
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
          <div className="empty-icon" aria-hidden="true">⚽</div>
          <h2>Waiting for the next whistle</h2>
          <p>No upcoming, live, or recently finished matches for your followed teams.</p>
          <p className="muted">
            Follow more teams from <Link className="text-link" to="/search">Find teams</Link>, and their
            matches will appear here automatically.
          </p>
        </div>
      ) : visibleMatches.length === 0 ? (
        <div className="card empty"><h2>No {filter === 'scheduled' ? 'upcoming' : filter} matches right now</h2><p className="muted">Check another filter to catch up on your teams.</p></div>
      ) : (
        <div className="grid">
          {visibleMatches.map((m) => (
            <MatchTile key={m.id} match={m} />
          ))}
        </div>
      )}
    </section>
  );
}
