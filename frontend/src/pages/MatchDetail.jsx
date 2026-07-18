import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../lib/api.js';
import EventFeed from '../components/EventFeed.jsx';

const POLL_MS = 15_000;

function statusText(m) {
  if (m.status === 'live') return `LIVE${m.minute != null ? ` · ${m.minute}'` : ''}`;
  if (m.status === 'finished') return 'Full time';
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
  const [data, setData] = useState(null); // { match, events } | null
  const [error, setError] = useState(null);
  const [notFound, setNotFound] = useState(false);
  const [muting, setMuting] = useState(false);
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      const d = await api.matchDetail(id);
      setData(d);
      setError(null);
    } catch (e) {
      if (/not found/i.test(e.message)) setNotFound(true);
      else setError(e.message);
    }
  }, [id]);

  useEffect(() => {
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
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [load]);

  async function toggleMute() {
    if (!data) return;
    const currentlyMuted = data.match.muted;
    setMuting(true);
    // optimistic
    setData((d) => ({ ...d, match: { ...d.match, muted: !currentlyMuted } }));
    try {
      if (currentlyMuted) await api.unmuteMatch(id);
      else await api.muteMatch(id);
    } catch (e) {
      setError(e.message);
      setData((d) => ({ ...d, match: { ...d.match, muted: currentlyMuted } })); // revert
    } finally {
      setMuting(false);
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

  if (!data) {
    return (
      <section>
        <p className="muted">
          <Link to="/">← Back</Link>
        </p>
        <div className="card skeleton" style={{ height: 180 }} />
      </section>
    );
  }

  const { match, events } = data;
  const live = match.status === 'live';

  return (
    <section className="match-detail">
      <p className="muted">
        <Link to="/">← Back to live matches</Link>
      </p>
      {error && <p className="error">{error}</p>}

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
