# Required deployed-AWS integration test fixtures

The merge-to-main deployment workflow runs four mandatory tests against the deployed environment after the API smoke checks. Configure the following GitHub repository **Variables** and **Secrets** before enabling the deployment workflow. Use isolated test-only users and slots; never point these tests at real patient records.

## Repository Variables

- `M6_TEST_DOCTOR_ID`: numeric doctor ID that owns the test slots.
- `M6_TEST_HEALTH_CAMP_SLOT_ID`: future, empty Health Camp slot with capacity exactly 3.
- `M6_TEST_IDEMPOTENCY_SLOT_ID`: future, empty capacity-1 slot.
- `M6_TEST_RESCHEDULE_PATIENT_ID`: numeric ID for the patient who owns the test appointment.
- `M6_TEST_RESCHEDULE_APPOINTMENT_ID`: confirmed future appointment ID for that patient.
- `M6_TEST_RESCHEDULE_TARGET_SLOT_ID`: future available slot for the same doctor, with no active hold.
- `M6_TEST_WAITLIST_SLOT_ID`: full future slot with capacity at least 1 and no existing waitlist entries.

## Repository Secrets

- `M6_TEST_PATIENT_TOKENS_JSON`: JSON array of at least 20 distinct patient JWTs for the concurrency test.
- `M6_TEST_IDEMPOTENCY_PATIENT_TOKEN`: JWT for a separate patient with no booking on the idempotency test slot.
- `M6_TEST_RESCHEDULE_PATIENT_TOKEN`: JWT for the patient owning the reschedule test appointment.
- `M6_TEST_WAITLIST_PATIENT_TOKENS_JSON`: JSON array of exactly three distinct patient JWTs, each with fewer than five active waitlists.

Tokens must be current when the workflow runs. Rotate them before expiry. These are test identities only.

## Test behavior and cleanup

1. Concurrency: submits 20+ simultaneous booking requests to the capacity-3 slot, expects exactly three successes and 409 for the rest, then cancels successful test appointments.
2. Idempotency: replays one client key twice and verifies the same appointment ID, then cancels the test appointment.
3. Saga compensation: directly starts the deployed state machine with an intentionally invalid original slot after a valid target claim. The release step fails, compensation must return the target `bookedCount` to its baseline, and the patient's original appointment remains unchanged.
4. Waitlist exclusivity: registers three patients, simulates a seat decrement, publishes `SlotSeatFreed`, verifies one live offer, expires it deterministically, and verifies the next FIFO patient receives the only live offer. The test leaves active waitlists and restores the fixture slot count.

The concurrency and idempotency tests require fresh, dedicated slots on every run. The reschedule test requires a confirmed appointment that remains available for the test. The waitlist test temporarily changes a dedicated slot's `bookedCount`; do not share its fixture with real users.
