# MediSlot Milestone 6 — implementation and deployment notes

## Architecture decisions retained

- RDS MySQL remains the appointment system of record.
- DynamoDB remains the concurrency authority for slots, waitlist entries, waitlist holds and notification dispatch claims.
- The Waitlist table keeps the physical name `medislot-${Environment}-waitlist` and uses `PK` / `SK` with the `PatientWaitlistIndex` GSI. The same table also stores patient metadata, per-patient/per-slot duplicate guards and short-lived saga step markers.
- Notification delivery is EventBridge → Lambda → SES. No SQS or SNS notification pipeline was added.
- The existing NotificationDispatch table is reused. A conditional put claims each deterministic dispatch key. If SES returns an uncertain outcome, the dispatch becomes `UNKNOWN` and is never automatically resent.
- No NAT Gateway, NAT instance or NAT route is created. The network stack adds interface endpoints for SSM, EventBridge, Step Functions and the SES API. Existing DynamoDB Gateway and Secrets Manager interface endpoints remain.
- No customer-managed KMS key is introduced.
- The existing doctor-triggered five-minute NO_SHOW operation is unchanged. The PDF's separate automatic 30-minute-after-end rule remains intentionally unimplemented pending an explicit product decision.

## Waitlist data model

| Attribute | Meaning |
|---|---|
| `PK=SLOT#<slotId>` | Groups registrations for one slot |
| `SK=ENTRY#<joinedAt>#<entryId>` | FIFO order |
| `GSI1PK=PATIENT#<patientId>` | Patient lookup partition |
| `GSI1SK=JOINED#<joinedAt>#<entryId>` | Patient registration order |
| `PK=PATIENT#<patientId>, SK=META` | Active registration count |
| `PK=PATIENT#<patientId>, SK=SLOT#<slotId>` | Duplicate-registration guard |
| `PK=SAGA#<sagaId>, SK=STEP#<step>` | Idempotent saga step markers |

A join uses one DynamoDB transaction to increment the active count (conditional maximum), create the entry, and create the duplicate guard. A leave/accept/expiry transaction updates the entry, decrements the count and removes the guard. A slot hold and its `OFFERED` entry are created in one transaction, so two promoters cannot claim the same seat concurrently.

## Reschedule Saga

`PATCH /appointments/{appointmentId}` validates ownership, appointment status, the configured two-reschedule limit and the configured six-hour cutoff. It starts a Standard Step Functions execution and returns HTTP 202 with an execution ARN. `GET /reschedule-executions?executionArn=...` checks status and verifies the patient identity against the execution input.

Saga order:

1. `ClaimTarget`: conditional target seat claim plus a deterministic saga marker.
2. `ReleaseOriginal`: conditional decrement of the original slot plus a deterministic marker.
3. `CommitAppointment`: one RDS transaction updates the appointment, increments `reschedule_count`, and writes a deterministic `AppointmentRescheduled` outbox event.
4. On a failure after target claim, `Compensate` restores the original seat if its release marker exists and releases the target seat if its claim marker exists.

The markers make state retries and compensation retries idempotent. The appointment row is updated in place; no duplicate appointment is created. The outbox event is dispatched by the existing appointment outbox scheduler. A small RDS `reschedule_locks` row prevents cancellation or outcome changes while a Saga is pending; it is marked `COMPLETED` or `FAILED` after the Saga finishes. If a Step Functions execution fails before compensation completes, leave the lock in `PENDING` until an operator verifies seat counts and the appointment before clearing it.

## Notification behavior

The notification consumer resolves recipients from RDS using the appointment or patient ID. It sends the eight agreed categories: booked (patient + doctor), rescheduled (patient + doctor), cancelled by patient (patient + doctor), cancelled by doctor (patient), waitlist offer issued (patient), waitlist offer expired (patient), 24-hour reminder (patient), and 2-hour reminder (patient).

The reminder dispatcher runs every five minutes and selects only still-CONFIRMED appointments in each reminder time bucket. The NotificationDispatch claim key is recipient- and event-specific. A sender must be configured at `/medislot/${Environment}/milestone6/ses-sender-email` before delivery is enabled.

## Deployment sequence

1. Back up the existing CloudFormation stacks and confirm the old Waitlist physical table is absent (the project owner reported deleting it manually). The table key schema change is a replacement-level DynamoDB change; do not deploy against a still-existing old-key table with the same physical name.
2. Run `database/MIGRATE_MILESTONE6_RESCHEDULE_LOCK.sql` against the existing RDS database before deploying the new Lambda/API code. The fresh schema also creates `reschedule_locks`. This table serializes reschedule starts against patient/doctor cancellation without changing the existing `reschedule_count` column.
3. After verifying an email address or domain identity in SES `us-east-1`, set `data.M6SesSenderEmail` in `Cloudformation/parameters.json` to that verified sender and redeploy the Data Stack. The CloudFormation-managed SSM parameter is `/medislot/${Environment}/milestone6/ses-sender-email`. Until then, the placeholder prevents send attempts without claiming a notification dispatch.
4. If the SES account is in the sandbox, verify recipient identities too or request SES production access.
5. Package Lambda code with `bash scripts/package_lambdas.sh`. The new `milestone6` package is generated from `lambdas/milestone6/`.
6. Upload versioned Lambda artifacts to the existing artifacts bucket using the repository's deployment workflow.
7. Deploy in existing stack order: Network → Security/IAM → Data → Application. Use CloudFormation change sets and inspect replacements before execution.
8. Confirm the SES API, SSM, EventBridge and Step Functions interface endpoints are `Available`; confirm there is no NAT Gateway or NAT route.
9. Confirm the Waitlist table keys, GSI and TTL, the Step Functions state machine, API methods, EventBridge rules and schedules.
10. Test authenticated API calls with a patient token. Verify the authorizer context, not a body-supplied patient ID, is used.

## SES configuration

- Verify the sender email or domain in the SES console for `us-east-1`.
- Request production access if the account remains in the SES sandbox and real patient/doctor recipient addresses are not verified.
- Set `data.M6SesSenderEmail` in `Cloudformation/parameters.json` and redeploy the Data Stack. The sender is never embedded in application source code.
- The Appointment API Lambda role has `ses:SendEmail` scoped to SES identity ARNs. SES API traffic uses the new SES interface endpoint; no NAT is needed.
- Test one appointment event and inspect the corresponding NotificationDispatch row. A successful attempt is `SENT`; an uncertain SES result is `UNKNOWN` and will not be resent.

## API endpoints added

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/slots/{slotId}/waitlist` | Patient | Join a full slot waitlist |
| DELETE | `/slots/{slotId}/waitlist` | Patient | Leave waitlist and release any offer |
| GET | `/patients/me/waitlists` | Patient | List caller's waitlist entries |
| POST | `/waitlist-offers/{offerId}/accept` | Patient | Accept the caller's live offer |
| PATCH | `/appointments/{appointmentId}` | Patient | Start asynchronous reschedule Saga |
| GET | `/reschedule-executions?executionArn=...` | Patient | Poll execution status |

## NO_SHOW rule conflict

The project PDF says an appointment still CONFIRMED 30 minutes after its appointment start time should be automatically marked NO_SHOW. The master implementation prompt explicitly says to preserve the existing manual five-minute-after-start doctor action and not add an automatic sweeper until this conflict is resolved. This implementation follows the master prompt and adds no scheduled NO_SHOW job.

## Verification status

- Python compilation: run locally before packaging.
- Milestone 5 regression subset plus Milestone 6 pure-rule tests: results recorded in `TEST_REPORT_MILESTONE6.md`.
- Four mandatory integration scenarios against deployed AWS were not run in this local environment. They must be executed after deployment with seeded test users and slots; do not treat local unit tests as a substitute.
- CloudFormation templates are YAML-parsed locally. This is not equivalent to AWS `validate-template`, change-set execution, or drift detection.
- SES sender identity, AWS deployment, live Step Functions execution, CloudWatch metrics/alarms, and DR restore drill require the user's AWS account and were not performed here.
