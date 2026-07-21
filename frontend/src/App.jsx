import { Routes, Route, Navigate, Link, useNavigate } from 'react-router-dom';
import { useAuth } from './lib/auth.jsx';
import Login from './pages/Login.jsx';
import Signup from './pages/Signup.jsx';
import Dashboard from './pages/Dashboard.jsx';
import MatchDetail from './pages/MatchDetail.jsx';
import Search from './pages/Search.jsx';
import Settings from './pages/Settings.jsx';

function Nav() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  return (
    <nav className="nav">
      <Link to="/" className="brand">⚽ Sports Alerts</Link>
      <div className="nav-links">
        <Link to="/">Dashboard</Link>
        <Link to="/search">Find teams</Link>
        <Link to="/settings">Settings</Link>
        {user ? (
          <button onClick={() => logout().then(() => navigate('/login'))}>Log out</button>
        ) : (
          <Link to="/login">Log in</Link>
        )}
      </div>
    </nav>
  );
}

export default function App() {
  return (
    <div className="app">
      <Nav />
      <main className="container">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/matches/:id" element={<MatchDetail />} />
          <Route path="/search" element={<Search />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
