import { Router } from 'express';
import { z } from 'zod';
import { config, pushSubscriptionsRepo } from '@sports/shared';
import { asyncHandler } from '../middleware/errorHandler.js';
import { requireAuth } from '../middleware/auth.js';

export const pushRoutes = Router();
pushRoutes.use(requireAuth);

const subscribeSchema = z.object({
  endpoint: z.string().url(),
  keys: z.object({
    p256dh: z.string(),
    auth: z.string(),
  }),
});

// Expose the VAPID public key so the client can subscribe.
pushRoutes.get('/vapid-public-key', (_req, res) => {
  res.json({ key: config.vapid.publicKey });
});

pushRoutes.post(
  '/subscribe',
  asyncHandler(async (req, res) => {
    const { endpoint, keys } = subscribeSchema.parse(req.body);
    await pushSubscriptionsRepo.upsertPushSubscription({
      userId: req.user.id,
      endpoint,
      p256dh: keys.p256dh,
      auth: keys.auth,
    });
    res.status(201).json({ ok: true });
  }),
);

pushRoutes.delete(
  '/subscribe',
  asyncHandler(async (req, res) => {
    const endpoint = z.string().url().parse(req.body?.endpoint);
    await pushSubscriptionsRepo.deletePushSubscriptionByEndpoint(req.user.id, endpoint);
    res.status(204).end();
  }),
);
