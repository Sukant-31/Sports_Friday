import jwt from 'jsonwebtoken';
import { config } from '@sports/shared';

// Reads the JWT from the httpOnly cookie and attaches req.user = { id }.
export function requireAuth(req, res, next) {
  const token = req.cookies?.[config.authCookieName];
  if (!token) {
    return res.status(401).json({ error: 'Not authenticated' });
  }
  try {
    const payload = jwt.verify(token, config.jwtSecret);
    req.user = { id: payload.sub };
    next();
  } catch {
    res.status(401).json({ error: 'Invalid or expired session' });
  }
}
