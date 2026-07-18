import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../lib/auth.js';

export default function Signup() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);

  async function onSubmit(e) {
    e.preventDefault();
    setError(null);
    try {
      await signup(email, password);
      navigate('/search');
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <form className="card form" onSubmit={onSubmit}>
      <h1>Create account</h1>
      {error && <p className="error">{error}</p>}
      <label>Email
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </label>
      <label>Password (min 8 chars)
        <input type="password" minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} required />
      </label>
      <button type="submit">Sign up</button>
      <p>Already registered? <Link to="/login">Log in</Link></p>
    </form>
  );
}
