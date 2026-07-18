import { Worker } from 'bullmq';
import {
  logger,
  redis,
  MATCH_EVENTS_QUEUE,
  subscriptionsRepo,
} from '@sports/shared';
import { sendPush } from './webPush.js';

const log = logger.child({ worker: 'notifier' });

// Human-readable notification copy per event type.
function buildNotification(type, detail) {
  const score = `${detail.homeScore ?? ''}–${detail.awayScore ?? ''}`.trim();
  switch (type) {
    case 'goal':
      return {
        title: '⚽ GOAL!',
        body: detail.player ? `${detail.player} (${detail.minute}')  ${score}` : `Score: ${score}`,
      };
    case 'card':
      return {
        title: detail.detail?.includes('Red') ? '🟥 Red card' : '🟨 Card',
        body: `${detail.player ?? ''} ${detail.minute ? `(${detail.minute}')` : ''}`.trim(),
      };
    case 'kickoff':
      return { title: '🟢 Kick-off', body: 'The match has started.' };
    case 'full_time':
      return { title: '🏁 Full-time', body: `Final score: ${score}` };
    default:
      return { title: 'Match update', body: '' };
  }
}

async function handleJob(job) {
  const { teamId, type, detail } = job.data;
  const targets = await subscriptionsRepo.findPushTargetsForEvent(teamId, type);
  if (targets.length === 0) return;

  const { title, body } = buildNotification(type, detail);
  const payload = { title, body, icon: '/icon.png' };

  const results = await Promise.allSettled(
    targets.map((t) => sendPush(t, payload)),
  );
  const failed = results.filter((r) => r.status === 'rejected');
  if (failed.length) {
    // Surface so BullMQ retries the job (dedup ledger prevents re-emitting on
    // a subsequent poll, so retries can't multiply distinct notifications).
    log.warn({ failed: failed.length, type }, 'some pushes failed');
    throw new Error(`${failed.length} push(es) failed`);
  }
  log.info({ type, sent: targets.length }, 'notifications sent');
}

export function startNotifier() {
  const worker = new Worker(MATCH_EVENTS_QUEUE, handleJob, {
    connection: redis,
    concurrency: 5,
  });
  worker.on('failed', (job, err) =>
    log.error({ jobId: job?.id, err }, 'notification job failed'),
  );
  log.info('notifier started');
  return worker;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  startNotifier();
}
