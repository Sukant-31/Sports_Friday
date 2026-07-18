import rateLimit from 'express-rate-limit';

// Tight limiter for auth endpoints (blunts credential stuffing).
export const authLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 20,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many attempts, try again later' },
});

// Looser limiter for search (protects the upstream sports-API budget).
export const searchLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 30,
  standardHeaders: true,
  legacyHeaders: false,
});
