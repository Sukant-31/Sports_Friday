import { Router } from 'express';
import { z } from 'zod';
import { asyncHandler } from '../middleware/errorHandler.js';
import { requireAuth } from '../middleware/auth.js';
import * as subs from '../services/subscriptionService.js';

export const subscriptionRoutes = Router();
subscriptionRoutes.use(requireAuth);

const createSchema = z.object({
  teamId: z.string().uuid(),
  notifyGoals: z.boolean().optional(),
  notifyCards: z.boolean().optional(),
  notifyMatchStatus: z.boolean().optional(),
});

const updateSchema = z.object({
  notifyGoals: z.boolean().optional(),
  notifyCards: z.boolean().optional(),
  notifyMatchStatus: z.boolean().optional(),
});

subscriptionRoutes.get(
  '/',
  asyncHandler(async (req, res) => {
    res.json({ subscriptions: await subs.list(req.user.id) });
  }),
);

subscriptionRoutes.post(
  '/',
  asyncHandler(async (req, res) => {
    const input = createSchema.parse(req.body);
    res.status(201).json({ subscription: await subs.create(req.user.id, input) });
  }),
);

subscriptionRoutes.patch(
  '/:id',
  asyncHandler(async (req, res) => {
    const id = z.string().uuid().parse(req.params.id);
    const prefs = updateSchema.parse(req.body);
    res.json({ subscription: await subs.update(req.user.id, id, prefs) });
  }),
);

subscriptionRoutes.delete(
  '/:id',
  asyncHandler(async (req, res) => {
    const id = z.string().uuid().parse(req.params.id);
    await subs.remove(req.user.id, id);
    res.status(204).end();
  }),
);
