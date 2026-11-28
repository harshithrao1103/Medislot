# Manual deployed-AWS Milestone 6 integration test fixtures

The GitHub Actions deployment workflow no longer creates disposable Milestone 6 patient accounts or runs the four mandatory deployed-AWS Milestone 6 integration tests. Those scenarios are intended to be verified manually after deployment, using isolated test-only slots. Never point manual tests at real patient appointments.

## Variables for manual integration testing only

The following environment variables are used only when you deliberately run `tests/integration/test_milestone6_aws.py` yourself. They are **not required as GitHub repository Variables** and are not read by the deployment workflow.

- `M6_TEST_DOCTOR_ID`: numeric doctor ID that owns the test slots.
- `M6_TEST_HEALTH_CAMP_SLOT_ID` (optional): future, empty, isolated Health Camp slot with capacity exactly 3. If omitted, the concurrency test scans the Slots table and automatically selects the only future, empty, AVAILABLE capacity-3 slot belonging to `M6_TEST_DOCTOR_ID`. If zero or multiple candidates are found, the test stops safely and asks you to configure this variable explicitly. Do not use a slot available to real patients.
- `M6_TEST_IDEMPOTENCY_SLOT_ID`: future, empty capacity-1 slot. It is used by the idempotency test and as the original booking slot for the reschedule compensation test.
- `M6_TEST_RESCHEDULE_TARGET_SLOT_ID`: different future available slot for the same doctor, with no active hold.
- `M6_TEST_WAITLIST_SLOT_ID`: full future slot with capacity at least 1 and no existing waitlist entries.
- `M6_TEST_PATIENT_TOKENS_JSON`: fresh patient access tokens supplied only for the manual test process. Do not commit tokens or put them in source files. Generate/login test patients through the application's supported authentication flow immediately before testing so the JWTs have not expired.

## Test behavior and cleanup

1. **Concurrency:** submits 20 simultaneous booking requests to the capacity-3 slot, expects exactly three bookings and 409 for the rest, then cancels successful test appointments.
2. **Idempotency:** replays one client key twice using a fresh patient, verifies the same appointment ID, then cancels the test appointment.
3. **Saga compensation:** creates a confirmed test appointment using a fresh patient and the idempotency fixture slot, starts the deployed state machine with an intentionally invalid original slot after a valid target claim, verifies compensation returns the target `bookedCount` to baseline and the original appointment remains confirmed, then cancels the test appointment.
4. **Waitlist exclusivity:** uses three fresh patients, simulates a seat decrement, publishes `SlotSeatFreed`, verifies one live offer, expires it deterministically, and verifies the next FIFO patient receives the only live offer. It removes the test patients' waitlist entries and restores the fixture slot count.

These tests mutate DynamoDB, RDS, EventBridge and Step Functions resources. Run them only against isolated test fixtures in an environment where you have reviewed the expected side effects. The tests do not run automatically during GitHub deployment.

## GitHub Actions EC2 dashboard deployment permission

The workflow still sends an SSM Run Command to the existing doctor-dashboard EC2 instance. The GitHub OIDC role `medislot_github_connection` must have the permissions in `MediSlot-GitHubActions-SSM-Policy.json`. If the role's inline policy is named `systemManager`, apply the updated policy document from the repository root with:

```powershell
aws iam put-role-policy `
  --role-name medislot_github_connection `
  --policy-name systemManager `
  --policy-document file://MediSlot-GitHubActions-SSM-Policy.json
```

This IAM update is separate from the ZIP and must be applied by an AWS identity allowed to update that role. It grants `ssm:SendCommand` only for the configured doctor-dashboard instance and `AWS-RunShellScript`, plus command-result read access.
