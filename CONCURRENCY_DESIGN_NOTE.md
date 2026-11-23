# MediSlot Booking Concurrency Design Note — Milestone 5

## Objective

For a slot with capacity `C`, confirmed bookings plus active holds must never exceed `C`, even when independent patient requests arrive concurrently. The design must allow requests to contend; it must not serialize all bookings behind a global lock.

## Slot record

The existing canonical DynamoDB Slots table is retained:

- Partition key: `doctorId` (string)
- Sort key: `slotKey` (`startTimeUTC#slotId`)
- Capacity fields: `capacity`, `bookedCount`, `heldCount`
- Optimistic concurrency field: `version`
- Availability field: `status`

No GSI or second Slots table is introduced.

## Reservation algorithm

1. Read the current slot consistently.
2. Reject if the record is missing, not `AVAILABLE`, or `bookedCount + heldCount >= capacity`.
3. Capture the observed `bookedCount`, `heldCount`, `capacity`, and `version`.
4. Issue one conditional DynamoDB update that increments `bookedCount` and `version` only if:
   - status remains `AVAILABLE`;
   - version still equals the observed version;
   - booked count and held count still equal the observed values;
   - capacity is unchanged; and
   - booked count is less than `capacity - heldCount`.
5. On conditional conflict, reread the record and retry with bounded exponential backoff plus jitter. The configured maximum is five retries after the initial attempt.
6. If the slot is now full or inactive, return HTTP 409. If the retry budget is exhausted, return HTTP 409 with a retryable contention message.

The condition uses observed counters and a computed `capacity - heldCount` threshold rather than relying on a non-atomic read/modify/write in application code. The update itself increments the counter and version atomically.

## Why this is not accidental serialization

There is no global mutex, queue, single-threaded worker, or global booking lock. Requests from different patients independently issue conditional updates to the same DynamoDB slot item. DynamoDB's conditional write is the contention point. A row lock is used only on the individual patient's RDS profile row while enforcing that patient's three-upcoming-appointments-per-doctor rule; it does not serialize bookings for different patients.

## Retry metric

Each conditional conflict that causes a retry emits one `BookingConflictRetries` CloudWatch Embedded Metric Format metric in namespace `medislot`. A full slot is rejected without retrying and therefore does not inflate the retry metric.

## Idempotency

The client supplies `Idempotency-Key`. The DynamoDB idempotency record is scoped as `patientId#key` and stores a request hash. A completed replay returns the original response. Reusing a key with a different request returns 409. The RDS appointment also stores a unique idempotency key, allowing recovery if the RDS commit succeeded but the Lambda lost the response before marking the DynamoDB record complete.

## RDS and DynamoDB boundary

RDS and DynamoDB do not share one ACID transaction. The implementation reserves a DynamoDB seat before inserting the RDS appointment. If the RDS operation fails, it attempts a compensating DynamoDB decrement. If the commit outcome is uncertain, it checks RDS by idempotency key before compensating. The RDS transaction inserts the appointment and its event-outbox row together.

Seat release from cancellation is driven by a `SlotSeatFreed` outbox event. The dispatcher uses a DynamoDB transaction to write a per-appointment release marker and decrement the slot counter atomically, so a retry cannot decrement the same seat twice.

## Verification

The repository includes a 20-worker, capacity-3 threaded test against a thread-safe fake of the conditional slot table. It verifies that exactly three reservation attempts succeed and the count never exceeds capacity. This is a unit-level model test, not proof of live DynamoDB behaviour.

Before the milestone checkpoint, run the same test against the deployed DynamoDB table using 20+ independent HTTP requests and verify:

- exactly three requests return success;
- all remaining requests return 409;
- `bookedCount` is exactly three;
- `BookingConflictRetries` is visible in CloudWatch; and
- the test does not route all requests through a serialized worker.

The mentor's independent parallel script remains a required acceptance check.
