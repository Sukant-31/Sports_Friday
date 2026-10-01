import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../lib/auth.jsx';

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState(null);

  async function onSubmit(e) {
    e.preventDefault();
    setError(null);
    try {
      await login(email, password);
      navigate('/');
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <form className="card form" onSubmit={onSubmit}>
      <h1>Log in</h1>
      {error && <p className="error">{error}</p>}
      <label>Email
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </label>
      <div className="password-field">
        <label htmlFor="login-password">Password</label>
        <div className="password-control">
          <input id="login-password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          <button type="button" className="password-toggle" aria-controls="login-password" aria-pressed={showPassword} onClick={() => setShowPassword((visible) => !visible)}>
            {showPassword ? 'Hide password' : 'Show password'}
          </button>
        </div>
      </div>
      <button type="submit">Log in</button>
      <p>No account? <Link to="/signup">Sign up</Link></p>
    </form>
  );
}
