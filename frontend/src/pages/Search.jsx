import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api.js';
import TeamCard from '../components/TeamCard.jsx';

export default function Search() {
  const [q, setQ] = useState('');
  const [results, setResults] = useState([]);
  const [error, setError] = useState(null);
  const [warning, setWarning] = useState(null);
  const [loading, setLoading] = useState(false);

  const [suggestions, setSuggestions] = useState([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [searched, setSearched] = useState(false);
  const requestId = useRef(0);
  const input = useRef(null);
  const debounce = useRef(null);

  async function search(query, id) {
    setLoading(true);
    setError(null);
    setWarning(null);
    try {
      const { teams, warning } = await api.searchTeams(query);
      if (id !== requestId.current) return;
      setSuggestions(teams);
      setResults(teams);
      setWarning(warning);
      setSearched(true);
    } catch (err) {
      if (id === requestId.current) setError(err.message);
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }

  useEffect(() => {
    const id = ++requestId.current;
    const query = q.trim();
    setActive(-1);
    setSuggestions([]);
    setLoading(false);
    setError(null);
    setWarning(null);
    setSearched(false);
    setResults([]);
    if (query.length < 3) return;
    const timer = setTimeout(() => search(query, id), 350);
    debounce.current = timer;
    return () => {
      clearTimeout(timer);
      ++requestId.current;
    };
  }, [q]);

  function select(team) {
    clearTimeout(debounce.current);
    ++requestId.current;
    setLoading(false);
    setResults([team]);
    input.current?.focus();
    setOpen(false);
    setActive(-1);
  }

  function onSearch(e) {
    e.preventDefault();
    if (open && active >= 0 && suggestions[active]) {
      select(suggestions[active]);
      return;
    }
    if (q.trim().length < 3) return;
    clearTimeout(debounce.current);
    setOpen(false);
    search(q.trim(), ++requestId.current);
  }

  function onKeyDown(e) {
    if (e.key === 'Escape') { setOpen(false); setActive(-1); }
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && suggestions.length) {
      e.preventDefault();
      setOpen(true);
      setActive((index) => e.key === 'ArrowDown'
        ? (index + 1) % suggestions.length
        : (index <= 0 ? suggestions.length - 1 : index - 1));
    }
  }

  async function follow(team) {
    try {
      const { subscription } = await api.subscribe(team.id);
      setWarning(subscription.warning);
      setResults((prev) => prev.map((t) => (t.id === team.id ? { ...t, followed: true } : t)));
      setSuggestions((prev) => prev.map((t) => (t.id === team.id ? { ...t, followed: true } : t)));
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <section>
      <h1>Find teams</h1>
      <form className="row" onSubmit={onSearch}>
        <div className="team-search-control" onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false);
        }}>
          <input
            ref={input}
            role="combobox"
            aria-label="Search teams"
            aria-describedby="team-search-status"
            aria-autocomplete="list"
            aria-expanded={open && suggestions.length > 0}
            aria-controls="team-suggestions"
            aria-activedescendant={open && active >= 0 ? `team-suggestion-${active}` : undefined}
            placeholder="Search a team, e.g. Barcelona"
            value={q}
            onFocus={() => setOpen(true)}
            onChange={(e) => { ++requestId.current; setQ(e.target.value); setOpen(true); }}
            onKeyDown={onKeyDown}
          />
          <ul id="team-suggestions" className="team-suggestions" role="listbox" aria-label="Matching teams" hidden={!open || suggestions.length === 0}>
            {suggestions.map((team, index) => (
              <li key={team.id} id={`team-suggestion-${index}`} role="option" aria-selected={active === index}>
                <button type="button" tabIndex={-1} onMouseDown={(e) => e.preventDefault()} onClick={() => select(team)}>
                  <span>{team.name}</span>{team.league && <small>{team.league}</small>}
                </button>
              </li>
            ))}
          </ul>
        </div>
        <button type="submit" disabled={loading || q.trim().length < 3}>{loading ? 'Searching…' : 'Search'}</button>
      </form>
      {error && <p className="error">{error}</p>}
      {warning && <p role="status" className="muted">{warning}</p>}
      <p id="team-search-status" role="status" className="muted">
        {q.trim().length < 3 ? 'Type at least 3 characters.' : loading ? 'Searching teams…' : searched && results.length === 0 && !error ? 'No matching teams. Try another name.' : ''}
      </p>
      <div className="grid">
        {results.map((t) => (
          <TeamCard key={t.id} team={t} onFollow={() => follow(t)} />
        ))}
      </div>
    </section>
  );
}
