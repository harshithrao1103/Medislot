# Cancelled appointment does not free the slot – cause and fix

## How freeing a seat works (outbox pattern)
1. Patient cancels -> `cancel_appointment()` marks the appointment CANCELLED_BY_PATIENT, records the refund
   and inserts a `SlotSeatFreed` row into `event_outbox` (status PENDING). It does NOT touch DynamoDB.
2. EventBridge Scheduler runs `dispatch_outbox()` every minute. For each `SlotSeatFreed` row it calls
   `_release_seat_once()`, which decrements `bookedCount` on the slot in DynamoDB.

## Bug
`_release_seat_once()` built its request in low-level DynamoDB format (`{"S": "..."}`, `{"N": "1"}`) but sent
it through `_slots.meta.client`. That client belongs to the boto3 *resource* and automatically serialises
native Python values, so every hand-written value was wrapped a second time as a Map. DynamoDB rejects the
transaction (ValidationException / key type mismatch), `dispatch_outbox()` raised, the row stayed PENDING
forever and `bookedCount` was never decremented -> the slot stayed "fully booked".

Aggravating factor: the dispatcher did not isolate failures, so one failing row aborted the whole batch,
and the same row was retried first on every run.

## Fix (lambdas/appointment_api/appointment_api_lambda.py)
- Added a plain `_ddb_client = boto3.client("dynamodb")` and used it in `_release_seat_once()`.
- `dispatch_outbox()` now handles each row in its own try/except, stores the error in `last_error`,
  increments `attempt_count` and carries on.

## After deploying
- Cancellations already made are still PENDING in `event_outbox`, so the next scheduler run should free their seats.
- Freeing is asynchronous by design: expect up to ~1 minute (schedule is `rate(1 minute)`) before the slot shows as available.
- Check: `SELECT event_type,status,attempt_count,last_error FROM event_outbox ORDER BY created_at DESC LIMIT 20;`
  `SlotSeatFreed` rows should become PUBLISHED. Logs: `/aws/lambda/medislot-dev-appointment-api`.

Not verified: boto3 and AWS access were unavailable here, so this was reviewed and compiled but not run against AWS.

## Update: immediate seat release on patient cancel
`cancel_appointment()` now calls `_release_seat_once()` right after the DB commit, so the slot becomes
available straight away instead of after the next scheduler run (up to 1 minute).
- Best effort: if it fails, the error is logged and the cancel still succeeds; the PENDING `SlotSeatFreed`
  outbox row is retried by the scheduler.
- Safe against double release: `_release_seat_once` is idempotent (`seat-release#<appointmentId>`), so the later
  dispatcher run sees it already done, skips the decrement, and just publishes the event.
