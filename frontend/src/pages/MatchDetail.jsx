import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../lib/api.js';
import { createLatestRequest } from '../lib/latestRequest.js';
import { exceptionalStatusLabel } from '../lib/fixtureStatus.js';
import EventFeed from '../components/EventFeed.jsx';

const POLL_MS = 15_000;

export function statusText(m) {
  if (m.status === 'live') return `LIVE${m.minute != null ? ` · ${m.minute}'` : ''}`;
  if (m.status === 'finished') return 'Full time';
  const exceptional = exceptionalStatusLabel(m.status);
  if (exceptional) return exceptional;
  if (m.starts_at) {
    return `Kick-off ${new Date(m.starts_at).toLocaleString([], {
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })}`;
  }
  return 'Scheduled';
}

export default function MatchDetail() {
  const { id } = useParams();
  return <MatchDetailRoute id={id} />;
}

// Route identity resets every state/ref before the new match is rendered.
export function MatchDetailRoute({ id }) {
  return <MatchDetailView key={id} id={id} />;
}

function MatchDetailView({ id }) {
  const [data, setData] = useState(null); // { match, events } | null
  const [error, setError] = useState(null);
  const [notFound, setNotFound] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [muting, setMuting] = useState(false);
  const timer = useRef(null);
  const reads = useRef(createLatestRequest());
  const mutations = useRef(createLatestRequest());
  const mutePending = useRef(false);

  const load = useCallback(async () => {
    if (mutePending.current) return;
    const request = reads.current.start();
    setRefreshing(true);
    try {
      const d = await api.matchDetail(id, { signal: request.signal });
      if (!request.isCurrent()) return;
      setData(d);
      setError(null);
      setNotFound(false);
    } catch (e) {
      if (!request.isCurrent()) return;
      if (e.status === 404 || /not found/i.test(e.message)) setNotFound(true);
      else setError(e.message);
    } finally {
      if (request.isCurrent()) setRefreshing(false);
    }
  }, [id]);

  useEffect(() => {
    setData(null);
    setError(null);
    setNotFound(false);
    setMuting(false);
    mutePending.current = false;
    const start = () => {
      stop();
      timer.current = setInterval(load, POLL_MS);
    };
    const stop = () => timer.current && clearInterval(timer.current);
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
    return () => {
      reads.current.cancel();
      mutations.current.cancel();
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [load]);

  async function toggleMute() {
    if (!data || String(data.match.id) !== id || mutePending.current) return;
    const mutation = mutations.current.start();
    reads.current.cancel();
    setRefreshing(false);
    mutePending.current = true;
    const currentlyMuted = data.match.muted;
    setMuting(true);
    // optimistic
    setData((d) => ({ ...d, match: { ...d.match, muted: !currentlyMuted } }));
    try {
      if (currentlyMuted) await api.unmuteMatch(id);
      else await api.muteMatch(id);
      if (mutation.isCurrent()) setError(null);
    } catch (e) {
      if (!mutation.isCurrent()) return;
      setError(e.message);
      setData((d) => ({ ...d, match: { ...d.match, muted: currentlyMuted } })); // revert
    } finally {
      if (mutation.isCurrent()) {
        mutePending.current = false;
        setMuting(false);
      }
    }
  }

  if (notFound) {
    return (
      <section>
        <p className="muted">
          <Link to="/">← Back</Link>
        </p>
        <div className="card empty">
          <p>Match not found, or you don&apos;t follow either team.</p>
        </div>
      </section>
    );
  }

  if (!data || String(data.match.id) !== id) {
    return (
      <section>
        <p className="muted">
          <Link to="/">← Back</Link>
        </p>
        {error ? (
          <div className="card empty">
            <p role="alert" className="error">Could not load this match: {error}</p>
            <button onClick={load} disabled={refreshing}>
              {refreshing ? 'Retrying…' : 'Retry'}
            </button>
          </div>
        ) : (
          <div className="card skeleton" style={{ height: 180 }} />
        )}
      </section>
    );
  }

  const { match, events } = data;
  const live = match.status === 'live';

  return (
    <section className="match-detail">
      <p className="muted">
        <Link to="/">← Back to your matches</Link>
      </p>
      {error && <p className="error">{error}</p>}
      {data.warning && <p role="status" className="muted">{data.warning}</p>}

      <div className="card scoreboard">
        <span className={`badge${live ? ' live' : ''}`}>
          {live && <span className="live-dot" />} {statusText(match)}
        </span>
        <div className="scoreboard-teams">
          <span className="team">{match.home_team}</span>
          <span className="score big">
            {match.home_score}<span className="dash">–</span>{match.away_score}
          </span>
          <span className="team">{match.away_team}</span>
        </div>
        <button
          className={`mute-btn${match.muted ? ' muted' : ''}`}
          onClick={toggleMute}
          disabled={muting}
        >
          {match.muted ? '🔕 Muted — notifications off' : '🔔 Mute this match'}
        </button>
      </div>

      <h2 className="timeline-title">Timeline</h2>
      {events.length === 0 ? (
        <p className="muted">No events yet.</p>
      ) : (
        <div className="card">
          <EventFeed events={events} />
        </div>
      )}
    </section>
  );
}
