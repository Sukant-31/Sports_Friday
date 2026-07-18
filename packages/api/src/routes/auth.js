import { Router } from 'express';
import { z } from 'zod';
import { config } from '@sports/shared';
import { asyncHandler } from '../middleware/errorHandler.js';
import { authLimiter } from '../middleware/rateLimit.js';
import {
  signup,
  login,
  signToken,
  authCookieOptions,
} from '../services/authService.js';

export const authRoutes = Router();

const credentials = z.object({
  email: z.string().email(),
  password: z.string().min(8).max(200),
});

authRoutes.post(
  '/signup',
  authLimiter,
  asyncHandler(async (req, res) => {
    const { email, password } = credentials.parse(req.body);
    const user = await signup({ email, password });
    res
      .cookie(config.authCookieName, signToken(user.id), authCookieOptions())
      .status(201)
      .json({ user });
  }),
);

authRoutes.post(
  '/login',
  authLimiter,
  asyncHandler(async (req, res) => {
    const { email, password } = credentials.parse(req.body);
    const user = await login({ email, password });
    res
      .cookie(config.authCookieName, signToken(user.id), authCookieOptions())
      .json({ user });
  }),
);

authRoutes.post('/logout', (req, res) => {
  res.clearCookie(config.authCookieName, { path: '/' }).json({ ok: true });
});
