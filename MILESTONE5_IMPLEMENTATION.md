# MediSlot Milestone 5 implementation notes

This change set adds the first Milestone 5 implementation on top of the supplied Milestone 4 ZIP.

## Scope implemented

- One `appointment_api` Lambda handles booking, caller-scoped appointment listing/detail, patient cancellation, doctor slot cancellation, refund history, and scheduled outbox dispatch.
- Booking uses the canonical `doctorId` / `slotKey` Slots table and conditional updates on `bookedCount`, `heldCount`, `capacity`, `status`, and `version`.
- Patient appointment limit is three upcoming confirmed appointments per doctor.
- `IdempotencyTable` is reused; completed requests replay the original appointment response.
- RDS appointments are the system of record. A transactional `event_outbox` record is committed with the appointment/cancellation/refund transaction.
- EventBridge receives `AppointmentBooked`, `AppointmentCancelled`, and `SlotSeatFreed`. No email consumer or notification workflow is created in Milestone 5.
- Refund percentages and windows are stored in `/medislot/<environment>/appointments/refund-policy` in SSM.
- Refunds are recorded in `refund_ledger`; no payment gateway is called.
- CloudWatch Embedded Metric Format logs publish `BookingConflictRetries` and `CancellationsByTier` metrics.
- The same Appointment API Lambda is invoked by EventBridge Scheduler every minute to dispatch pending outbox records.
- A private EventBridge interface VPC endpoint is added. No NAT Gateway is introduced.

## Required database preparation

For an existing RDS database, take and verify a snapshot, inspect for duplicate active patient/slot bookings and duplicate refund rows, then run `database/MIGRATE_MILESTONE5_BOOKING.sql` once.

Do not run that migration on a fresh database already created from the updated `database/schema.sql`. The migration is intentionally explicit and is not safe to run twice.

The generated `active_booking_guard` plus unique key prevents a patient from having two active bookings for the same slot while allowing a new booking after cancellation.

## API endpoints added

- `POST /appointments` (patient; requires `Idempotency-Key` header; body contains `doctorId`, `slotId`)
- `GET /appointments` (patient or doctor; caller-scoped)
- `GET /appointments/{appointmentId}` (caller-scoped)
- `DELETE /appointments/{appointmentId}` (patient)
- `GET /patients/me/refunds` (patient)
- `GET /doctors/me/appointments?from=&to=&status=` (doctor)
- `DELETE /doctors/me/slots/{slotId}` (doctor)
- `POST /doctors/me/appointments/{appointmentId}/outcome` (doctor; COMPLETED or NO_SHOW)

The patient doctor page now calls a Flask same-origin booking route which forwards the request and idempotency key to API Gateway.

## Deployment order

1. Review and back up RDS; apply the one-time SQL migration to the existing database.
2. Deploy the updated Data Stack (EventBridge bus and refund SSM parameter).
3. Deploy the updated Security/IAM Stack (Appointment Lambda role, Scheduler role, EventBridge VPC endpoint).
4. Deploy the updated Application Stack (Appointment API Lambda, API routes, outbox schedule).
5. Run the CI workflow and then perform live AWS integration tests.

Do not deploy before applying the RDS migration: the new Lambda expects the added appointment columns and `event_outbox` table.

## Important verification boundary

Local unit tests exercise refund cutoff logic and a thread-safe model of the conditional slot reservation algorithm. They do not replace the required live DynamoDB test with 20+ independent requests against a capacity-3 slot. The live concurrency, RDS transaction, EventBridge publication, private VPC connectivity, and end-to-end dashboard flows must be verified in AWS before declaring Milestone 5 complete.
