# DynamoDB data model — Milestone 6

## Waitlist table

Physical name: `medislot-${Environment}-waitlist-m6`.

- PK `SLOT#slotId`, SK `ENTRY#joinedAt#entryId`: FIFO registration queue.
- GSI `PatientWaitlistIndex`: GSI1PK `PATIENT#patientId`, GSI1SK `JOINED#joinedAt#entryId`.
- Patient counter item: PK `PATIENT#patientId`, SK `META`, attribute `activeWaitlistCount`.
- Duplicate guard item: PK `PATIENT#patientId`, SK `SLOT#slotId`.
- Saga marker item: PK `SAGA#sagaId`, SK `STEP#stepName`.
- TTL attribute: `ttlExpiresAt`.

The five-entry cap is enforced by a transactional counter update, not by an eventually consistent GSI count. Duplicate registration is guarded by a transactional conditional put.

## Slots table additions

An active exclusive waitlist hold is represented directly on the existing slot item using `holdOfferId`, `holdPatientId`, and `holdExpiresAt`. Normal appointment booking rejects a live hold in its conditional update. The hold and waitlist offer state are created together in a DynamoDB transaction.

## NotificationDispatch table

Existing physical table and `dispatchId` partition key are reused. Dispatch IDs are SHA-256 hashes of event identity, notification type and recipient identity. `PROCESSING`, `SENT` and `UNKNOWN` represent the at-most-once send-attempt lifecycle.

## Access patterns

1. Query a slot's FIFO registrations by PK.
2. Query a patient's registrations by `PatientWaitlistIndex`.
3. Conditionally update the patient metadata count.
4. Conditionally claim/release a slot hold.
5. Read deterministic saga markers to make state retries and compensation idempotent.
6. Conditionally claim a notification dispatch before calling SES.

## RDS coordination record

The RDS `reschedule_locks` table is keyed by `appointment_id` and records `saga_id`, `patient_id`, and `PENDING` / `COMPLETED` / `FAILED`. It coordinates the async Saga with cancellation/outcome updates; it does not replace or duplicate `appointments.reschedule_count`.
