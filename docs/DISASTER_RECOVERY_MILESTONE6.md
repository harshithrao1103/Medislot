# Disaster recovery runbook

## Backup posture

- RDS automated backups and retention are configured in the existing Data Stack. Confirm the deployed DB instance's backup retention and latest restorable time before a recovery exercise.
- DynamoDB point-in-time recovery is enabled for the existing Slots, Waitlist and NotificationDispatch tables by CloudFormation.
- Do not restore over production tables without an approved maintenance window and a verified backup timestamp.

## Restore procedure (not yet performed)

1. Record the incident time, affected environment, RDS latest restorable time and DynamoDB PITR timestamp.
2. Restore RDS to a new instance from the selected point in time; do not replace the original instance until validation completes.
3. Restore Slots, Waitlist and NotificationDispatch to new DynamoDB table names using PITR.
4. Validate appointment counts, refund ledger consistency, slot booked counts, active holds, waitlist FIFO order and dispatch state.
5. Update CloudFormation parameters/exports in a planned change set to point the application to restored resources; preserve private networking and no-NAT constraints.
6. Run smoke tests for patient/doctor auth, booking, cancellation, waitlist, reschedule and reminders.
7. Record measured elapsed restore time, data-loss window, validation output and approver.

**Restore drill status:** Not performed in this environment. No restore time is claimed.
