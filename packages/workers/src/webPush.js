import webpush from 'web-push';
import { config, logger, pushSubscriptionsRepo } from '@sports/shared';

let configured = false;

function ensureConfigured() {
  if (configured) return;
  if (!config.vapid.publicKey || !config.vapid.privateKey) {
    throw new Error('VAPID keys are not set — run `pnpm gen:vapid`');
  }
  webpush.setVapidDetails(
    config.vapid.subject,
    config.vapid.publicKey,
    config.vapid.privateKey,
  );
  configured = true;
}

// Send one push. On an expired subscription (404/410) delete it and resolve
// (not an error the queue should retry). Other failures throw so BullMQ retries.
export async function sendPush(target, payload) {
  ensureConfigured();
  const subscription = {
    endpoint: target.endpoint,
    keys: { p256dh: target.p256dh, auth: target.auth },
  };
  try {
    await webpush.sendNotification(subscription, JSON.stringify(payload));
  } catch (err) {
    if (err.statusCode === 404 || err.statusCode === 410) {
      logger.info({ pushId: target.push_id }, 'pruning expired push subscription');
      await pushSubscriptionsRepo.deletePushSubscriptionById(target.push_id);
      return;
    }
    throw err;
  }
}
