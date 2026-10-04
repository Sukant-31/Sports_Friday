# Web Push delivery outcomes

A generated event is a durable `match_events` row. A provider-accepted push is
recorded in `notification_deliveries` only after the real Web Push request
succeeds. The cron summary calls this count `notifications.accepted`, replacing
its former misleading `notifications.delivered` name. `display_confirmation`
is `unavailable`: acceptance does not establish browser receipt, notification
display, or that a person saw it (RFC 8030 section 5).

The legacy `match_events.delivered_at` column means processing completed, not
browser display. It can be set when no recipients qualify, when subscriptions
expire, or when all remaining requests have terminal outcomes.

HTTP 404/410 still remove the expired subscription. Other 3xx and 4xx responses
(except retryable 408/425/429) are terminal for that event and subscription.
Invalid legacy destinations or invalid transport inputs are terminal as well.
They are reported as failures on the attempt that encounters them; other devices
continue processing. HTTP 5xx, network errors, and missing server configuration
remain retryable using the existing pending-event/worker paths. Terminal failures
are not retried unchanged; repairing a device/configuration does not replay its
old terminal events. Console transport is recorded as simulation, not acceptance.

Migration `009_push_delivery_outcomes.sql` adds a separate terminal-outcome ledger;
it leaves accepted receipts and all existing data intact. The existing serverless
notification-schema initializer and worker startup install it idempotently, or it
can be applied by the normal migration runner. The packaged SQL copy must match
the root migration.

Event row locks serialize simultaneous workers. Lifecycle match locks also
serialize team fan-out. Each device uses its own transaction: its accepted or terminal outcome is
committed immediately, before sending to the next device. A crash on a later
device therefore cannot roll back an earlier device's outcome. Retries skip both
accepted and terminal outcomes and attempt only unresolved devices. Locks are
held through one device's send and receipt commit, not an entire event fan-out.
Event processing completion is recorded separately after all selected devices
have been resolved.
Foreign-key cleanup remains unchanged. Existing historical receipts retain their
original meaning; this change cannot prove how historical console sends behaved.

There is an unavoidable uncertain-outcome window: a provider may accept a request
but the response can be lost, or the process can stop before committing its
receipt. A subsequent retry can then send again. Cancelling the async task also cannot
prove that its synchronous network thread stopped before provider acceptance. Standard Web Push has no
application idempotency key or proof of visual display, so this design guarantees
serialization and receipt-based retry deduplication, not exactly-once delivery
across crashes/network ambiguity. Existing notification tags are unchanged.
