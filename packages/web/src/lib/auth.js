import { createContext, useContext, useState, useCallback } from 'react';
import { api } from './api.js';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  // We don't have a /me endpoint in v1; treat presence of a prior login in
  // this tab as the signal. A real app would add GET /api/auth/me.
  const [user, setUser] = useState(null);

  const login = useCallback(async (email, password) => {
    const { user } = await api.login(email, password);
    setUser(user);
    return user;
  }, []);

  const signup = useCallback(async (email, password) => {
    const { user } = await api.signup(email, password);
    setUser(user);
    return user;
  }, []);

  const logout = useCallback(async () => {
    await api.logout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, login, signup, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
