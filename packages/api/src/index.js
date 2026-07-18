import { createApp } from './app.js';
import { config, logger, closePool, closeRedis } from '@sports/shared';

const app = createApp();

const server = app.listen(config.apiPort, () => {
  logger.info({ port: config.apiPort }, 'API listening');
});

async function shutdown(signal) {
  logger.info({ signal }, 'shutting down API');
  server.close(async () => {
    await closePool();
    await closeRedis();
    process.exit(0);
  });
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
