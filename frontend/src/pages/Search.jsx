import { useState } from 'react';
import { api } from '../lib/api.js';
import TeamCard from '../components/TeamCard.jsx';

export default function Search() {
  const [q, setQ] = useState('');
  const [results, setResults] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  async function onSearch(e) {
    e.preventDefault();
    if (q.trim().length < 2) return;
    setLoading(true);
    setError(null);
    try {
      const { teams } = await api.searchTeams(q.trim());
      setResults(teams);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function follow(team) {
    try {
      await api.subscribe(team.id);
      setResults((prev) => prev.map((t) => (t.id === team.id ? { ...t, followed: true } : t)));
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <section>
      <h1>Find teams</h1>
      <form className="row" onSubmit={onSearch}>
        <input
          placeholder="Search a team, e.g. Arsenal"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button type="submit" disabled={loading}>{loading ? 'Searching…' : 'Search'}</button>
      </form>
      {error && <p className="error">{error}</p>}
      <div className="grid">
        {results.map((t) => (
          <TeamCard key={t.id} team={t} onFollow={() => follow(t)} />
        ))}
      </div>
    </section>
  );
}
