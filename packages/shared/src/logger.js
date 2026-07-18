import pino from 'pino';
import { config, isProd } from './config.js';

export const logger = pino({
  level: config.logLevel,
  // Pretty output in dev; JSON in prod for log aggregation.
  transport: isProd
    ? undefined
    : { target: 'pino-pretty', options: { colorize: true, translateTime: 'HH:MM:ss' } },
});

export function childLogger(bindings) {
  return logger.child(bindings);
}
