# Deployed-AWS Milestone 6 integration fixtures

The merge-to-main deployment workflow runs four mandatory tests against the deployed environment after the API smoke checks. Use isolated test-only slots; never point these tests at real patient appointments.

## GitHub repository Variables

- `M6_TEST_DOCTOR_ID`: numeric doctor ID that owns the test slots.
- `M6_TEST_HEALTH_CAMP_SLOT_ID` (optional): future, empty, isolated Health Camp slot with capacity exactly 3. If omitted, the concurrency test scans the Slots table and automatically selects the only future, empty, AVAILABLE capacity-3 slot belonging to `M6_TEST_DOCTOR_ID`. If zero or multiple candidates are found, the test stops safely and asks you to configure this variable explicitly. Do not use a slot available to real patients.
- `M6_TEST_IDEMPOTENCY_SLOT_ID`: future, empty capacity-1 slot. It is used by the idempotency test and as the original booking slot for the reschedule compensation test.
- `M6_TEST_RESCHEDULE_TARGET_SLOT_ID`: future available slot for the same doctor, with no active hold.
- `M6_TEST_WAITLIST_SLOT_ID`: full future slot with capacity at least 1 and no existing waitlist entries.

Patient JWTs are **not** configured as GitHub Secrets. Immediately before the integration tests, `tests/integration/setup_milestone6_patients.py` registers 20 disposable patients through `POST /auth/patients/register`, logs each in through `POST /auth/patients/login`, and writes the fresh access tokens only to the current GitHub Actions job environment. The tokens are never printed or committed. Each run gets new accounts and tokens, so the 15-minute JWT expiry does not require manual rotation.

## Test behavior and cleanup

1. **Concurrency:** submits 20 simultaneous booking requests to the capacity-3 slot, expects exactly three bookings and 409 for the rest, then cancels successful test appointments.
2. **Idempotency:** replays one client key twice using a fresh patient, verifies the same appointment ID, then cancels the test appointment.
3. **Saga compensation:** creates a confirmed test appointment using a fresh patient and the idempotency fixture slot, starts the deployed state machine with an intentionally invalid original slot after a valid target claim, verifies compensation returns the target `bookedCount` to baseline and the original appointment remains confirmed, then cancels the test appointment.
4. **Waitlist exclusivity:** uses three fresh patients, simulates a seat decrement, publishes `SlotSeatFreed`, verifies one live offer, expires it deterministically, and verifies the next FIFO patient receives the only live offer. It removes the test patients' waitlist entries and restores the fixture slot count.

The fixture slots must be isolated from real users and available for automated mutation. The capacity-3 concurrency fixture may be discovered automatically only when exactly one matching future empty slot exists; all other fixtures remain explicitly configured. The CI-created patient accounts are disposable test identities; this project currently has no patient-account deletion API, so each workflow run leaves those uniquely named accounts in the database. They contain synthetic data only.

## GitHub Actions EC2 dashboard deployment permission

The workflow also sends an SSM Run Command to the existing doctor-dashboard EC2 instance. The GitHub OIDC role `medislot_github_connection` must have the permissions in `MediSlot-GitHubActions-SSM-Policy.json`. If the role's inline policy is named `systemManager`, apply the updated policy document from the repository root with:

```powershell
aws iam put-role-policy `
  --role-name medislot_github_connection `
  --policy-name systemManager `
  --policy-document file://MediSlot-GitHubActions-SSM-Policy.json
```

This IAM update is separate from the ZIP and must be applied by an AWS identity allowed to update that role. It grants `ssm:SendCommand` only for the configured doctor-dashboard instance and `AWS-RunShellScript`, plus command-result read access.
