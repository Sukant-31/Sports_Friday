// Thin fetch wrapper. credentials:'include' sends the httpOnly auth cookie.
// In dev, Vite proxies /api -> http://localhost:4000 (same-origin cookies).

async function request(path, { method = 'GET', body } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: 'include',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error ?? `Request failed (${res.status})`);
  }
  return data;
}

export const api = {
  signup: (email, password) => request('/auth/signup', { method: 'POST', body: { email, password } }),
  login: (email, password) => request('/auth/login', { method: 'POST', body: { email, password } }),
  logout: () => request('/auth/logout', { method: 'POST' }),

  searchTeams: (q) => request(`/teams/search?q=${encodeURIComponent(q)}`),

  listSubscriptions: () => request('/subscriptions'),
  subscribe: (teamId, prefs = {}) => request('/subscriptions', { method: 'POST', body: { teamId, ...prefs } }),
  updateSubscription: (id, prefs) => request(`/subscriptions/${id}`, { method: 'PATCH', body: prefs }),
  unsubscribe: (id) => request(`/subscriptions/${id}`, { method: 'DELETE' }),

  liveMatches: () => request('/matches/live'),

  vapidKey: () => request('/push/vapid-public-key'),
  registerPush: (subscription) =>
    request('/push/subscribe', { method: 'POST', body: subscription }),
};
