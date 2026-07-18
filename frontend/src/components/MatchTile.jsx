export default function MatchTile({ match }) {
  const live = match.status === 'live';
  return (
    <div className="card match-tile">
      <div className="teams">
        <span>{match.home_team}</span>
        <span className="score">{match.home_score} – {match.away_score}</span>
        <span>{match.away_team}</span>
      </div>
      <div className={`status ${live ? 'live' : ''}`}>
        {live ? 'LIVE' : match.status}
      </div>
    </div>
  );
}
