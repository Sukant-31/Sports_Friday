import { Router } from 'express';
import { z } from 'zod';
import { asyncHandler } from '../middleware/errorHandler.js';
import { requireAuth } from '../middleware/auth.js';
import { searchLimiter } from '../middleware/rateLimit.js';
import { searchTeams } from '../services/teamService.js';

export const teamRoutes = Router();

teamRoutes.get(
  '/search',
  requireAuth,
  searchLimiter,
  asyncHandler(async (req, res) => {
    const q = z.string().min(2).max(60).parse(req.query.q);
    const teams = await searchTeams(q);
    res.json({ teams });
  }),
);
