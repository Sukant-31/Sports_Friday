import { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { api } from './api.js';
import { authenticateWithPushCleanup, logoutWithPushCleanup } from '../pushLifecycle.js';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  // True until the initial GET /auth/me resolves, so callers can avoid
  // flashing a "log in" prompt while a valid session cookie is still being
  // checked (e.g. after a page refresh).
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .me()
      .then(({ user }) => setUser(user))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email, password) => {
    const { user } = await authenticateWithPushCleanup(api.login, email, password);
    setUser(user);
    return user;
  }, []);

  const signup = useCallback(async (email, password) => {
    const { user } = await authenticateWithPushCleanup(api.signup, email, password);
    setUser(user);
    return user;
  }, []);

  const logout = useCallback(async () => {
    await logoutWithPushCleanup();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, signup, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
