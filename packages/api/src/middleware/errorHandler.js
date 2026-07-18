import { ZodError } from 'zod';
import { logger } from '@sports/shared';

// Central error handler. Keep route handlers free of try/catch by wrapping
// async handlers with asyncHandler (below) and throwing.
export function errorHandler(err, _req, res, _next) {
  if (err instanceof ZodError) {
    return res.status(400).json({ error: 'Validation failed', details: err.flatten() });
  }
  if (err.status && err.expose) {
    return res.status(err.status).json({ error: err.message });
  }
  logger.error({ err }, 'unhandled API error');
  res.status(500).json({ error: 'Internal server error' });
}

export function httpError(status, message) {
  const err = new Error(message);
  err.status = status;
  err.expose = true;
  return err;
}

// Wrap an async handler so thrown/rejected errors reach errorHandler.
export function asyncHandler(fn) {
  return (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);
}
