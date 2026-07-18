const ICON = { goal: '⚽', card: '🟨', kickoff: '🟢', full_time: '🏁' };

function iconFor(ev) {
  if (ev.type === 'card' && (ev.detail || '').includes('Red')) return '🟥';
  return ICON[ev.type] ?? '•';
}

function labelFor(ev) {
  const score =
    ev.home_score != null && ev.away_score != null
      ? `  (${ev.home_score}–${ev.away_score})`
      : '';
  switch (ev.type) {
    case 'goal':
      return `${ev.player ?? 'Goal'}${score}`;
    case 'card':
      return `${ev.player ?? 'Card'}${ev.detail ? ` — ${ev.detail}` : ''}`;
    case 'kickoff':
      return 'Kick-off';
    case 'full_time':
      return `Full-time${score}`;
    default:
      return ev.type;
  }
}

export default function EventFeed({ events }) {
  if (!events || events.length === 0) return null;
  return (
    <ul className="events">
      {events.map((ev, i) => (
        <li key={i} className="event">
          <span className="event-icon">{iconFor(ev)}</span>
          {ev.minute != null && <span className="event-min">{ev.minute}&apos;</span>}
          <span className="event-text">{labelFor(ev)}</span>
        </li>
      ))}
    </ul>
  );
}
