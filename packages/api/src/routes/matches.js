import { Router } from 'express';
import { matchesRepo } from '@sports/shared';
import { asyncHandler } from '../middleware/errorHandler.js';
import { requireAuth } from '../middleware/auth.js';

export const matchRoutes = Router();

matchRoutes.get(
  '/live',
  requireAuth,
  asyncHandler(async (req, res) => {
    const matches = await matchesRepo.findLiveMatchesForUser(req.user.id);
    res.json({ matches });
  }),
);
