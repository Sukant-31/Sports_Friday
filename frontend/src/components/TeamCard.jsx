export default function TeamCard({ team, onFollow }) {
  return (
    <div className="card team-card">
      <div>
        <strong>{team.name}</strong>
        {team.league && <div className="muted">{team.league}</div>}
      </div>
      <button onClick={onFollow} disabled={team.followed}>
        {team.followed ? 'Following' : 'Follow'}
      </button>
    </div>
  );
}
