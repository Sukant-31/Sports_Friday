// Loads and validates environment configuration. Throws early (at import
// time) if anything required is missing, so a service never boots half-configured.

function required(name) {
  const value = process.env[name];
  if (value === undefined || value === '') {
    throw new Error(`Missing required env var: ${name}`);
  }
  return value;
}

function optional(name, fallback) {
  const value = process.env[name];
  return value === undefined || value === '' ? fallback : value;
}

export const config = {
  env: optional('NODE_ENV', 'development'),
  logLevel: optional('LOG_LEVEL', 'info'),

  databaseUrl: required('DATABASE_URL'),
  redisUrl: required('REDIS_URL'),

  jwtSecret: required('JWT_SECRET'),
  authCookieName: optional('AUTH_COOKIE_NAME', 'sports_token'),

  sportsApi: {
    key: optional('SPORTS_API_KEY', ''),
    baseUrl: optional('SPORTS_API_BASE_URL', 'https://v3.football.api-sports.io'),
  },

  vapid: {
    publicKey: optional('VAPID_PUBLIC_KEY', ''),
    privateKey: optional('VAPID_PRIVATE_KEY', ''),
    subject: optional('VAPID_SUBJECT', 'mailto:you@example.com'),
  },

  apiPort: Number(optional('API_PORT', '4000')),
  pollIntervalMs: Number(optional('POLL_INTERVAL_MS', '20000')),
  corsOrigin: optional('CORS_ORIGIN', 'http://localhost:5173')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean),
};

export const isProd = config.env === 'production';
