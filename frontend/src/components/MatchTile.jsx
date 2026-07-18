import { useEffect, useRef, useState } from 'react';
import EventFeed from './EventFeed.jsx';

function StatusBadge({ match }) {
  if (match.status === 'live') {
    return (
      <span className="badge live">
        <span className="live-dot" /> LIVE{match.minute != null ? ` ${match.minute}'` : ''}
      </span>
    );
  }
  if (match.status === 'finished') return <span className="badge">FT</span>;
  // scheduled
  const kickoff = match.starts_at
    ? new Date(match.starts_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : null;
  return <span className="badge">{kickoff ? `KO ${kickoff}` : 'Scheduled'}</span>;
}

export default function MatchTile({ match }) {
  const scoreKey = `${match.home_score}-${match.away_score}`;
  const prevScore = useRef(scoreKey);
  const [flash, setFlash] = useState(false);

  // Flash the score briefly when it changes between polls.
  useEffect(() => {
    if (prevScore.current !== scoreKey) {
      prevScore.current = scoreKey;
      setFlash(true);
      const t = setTimeout(() => setFlash(false), 1600);
      return () => clearTimeout(t);
    }
  }, [scoreKey]);

  return (
    <div className="card match-tile">
      <div className="match-head">
        <StatusBadge match={match} />
      </div>
      <div className="match-teams">
        <span className="team">{match.home_team}</span>
        <span className={`score${flash ? ' flash' : ''}`}>
          {match.home_score}<span className="dash">–</span>{match.away_score}
        </span>
        <span className="team">{match.away_team}</span>
      </div>
      <EventFeed events={match.events} />
    </div>
  );
}
