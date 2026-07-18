// Runs both workers in one process for dev/simple deploys. In production they
// can be split into separate services (see deploy/Dockerfile.workers) by
// invoking `poller.js` / `notifier.js` directly.
import { logger, closePool, closeRedis } from '@sports/shared';
import { startPoller } from './poller.js';
import { startNotifier } from './notifier.js';

const interval = startPoller();
const notifier = startNotifier();

async function shutdown(signal) {
  logger.info({ signal }, 'shutting down workers');
  clearInterval(interval);
  await notifier.close();
  await closePool();
  await closeRedis();
  process.exit(0);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
