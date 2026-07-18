import express from 'express';
import cookieParser from 'cookie-parser';
import cors from 'cors';
import { config } from '@sports/shared';

import { authRoutes } from './routes/auth.js';
import { teamRoutes } from './routes/teams.js';
import { subscriptionRoutes } from './routes/subscriptions.js';
import { matchRoutes } from './routes/matches.js';
import { pushRoutes } from './routes/push.js';
import { errorHandler } from './middleware/errorHandler.js';

// App factory so tests can spin up an instance without binding a port.
export function createApp() {
  const app = express();

  app.use(express.json());
  app.use(cookieParser());
  app.use(
    cors({
      origin: config.corsOrigin,
      credentials: true,
    }),
  );

  app.get('/health', (_req, res) => res.json({ ok: true }));

  app.use('/api/auth', authRoutes);
  app.use('/api/teams', teamRoutes);
  app.use('/api/subscriptions', subscriptionRoutes);
  app.use('/api/matches', matchRoutes);
  app.use('/api/push', pushRoutes);

  app.use(errorHandler);
  return app;
}
