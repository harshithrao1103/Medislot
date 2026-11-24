# MediSlot appointment UX updates

## Implemented

- Patient cancellation form now displays the agreed refund tiers and a dynamically calculated estimate based on the appointment start time and consultation fee. The backend remains authoritative and recalculates the final refund during cancellation.
- Patient slot discovery no longer filters out slots with zero free seats. Fully booked slots are returned and shown as `Already booked`; booking controls are omitted for those slots. Patient identity is never exposed.
- Doctor appointment list responses now include the canonical slot end time from DynamoDB. The doctor dashboard keeps `Mark completed` visible but disabled until that end time.
- The appointment outcome Lambda independently validates the scheduled end time before accepting `COMPLETED`, preventing early completion through direct API calls.
- Existing five-minute NO_SHOW validation and transactional outbox seat-release flow are preserved.

## Database / infrastructure

- No database migration or new table is required.
- Existing `refund_ledger` and DynamoDB slot inventory are reused.
- No NAT Gateway or new AWS network resource is introduced.

## Validation performed

- Python compilation passed for the changed Flask and Lambda modules.
- Jinja parsing passed for patient appointments, doctor appointments, and patient doctor templates.
- Targeted tests: 11 passed for patient slot display/refund policy, and 5 passed for idempotency, concurrency, and slot reconciliation.

## Deployment note

Deploy the updated `patient_domain` Lambda and `appointment_api` Lambda, then update the Flask application on EC2. No CloudFormation change is included. Test with real RDS and DynamoDB data after deployment.
