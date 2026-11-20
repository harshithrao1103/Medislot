# Slots table primary-key migration

The application now uses `medislot-${Environment}-slots-v2` with:

- Partition key: `doctorId` (String)
- Sort key: `slotKey` (String), formatted as `startTimeUTC#slotId`
- `slotId` and `startTimeUTC` remain normal item attributes.
- No `DoctorStartIndex` GSI is created or queried.

## CloudFormation deployment order

The Data Stack exports both names during migration so its existing cross-stack export is not changed while the Application Stack still imports it.

1. Deploy `Cloudformation/Data_Stack.yaml` first. This creates the new `slots-v2` table and exports `SlotInventoryTableV2Name`. The old `medislot-${Environment}-slots` table is retained by the existing `DeletionPolicy` and `UpdateReplacePolicy`.
2. If the old table contains slot records that must be preserved, migrate them to the new table before switching the application. For each old item, add `slotKey = startTimeUTC + '#' + slotId` and write it using `(doctorId, slotKey)` as the key. Do not copy an item if the new table already contains that key.
3. Deploy `Cloudformation/Application_Stack.yaml`. All Lambdas now import `SlotInventoryTableV2Name` and query/update the new table.
4. Verify doctor schedule retrieval, patient availability retrieval, slot materialisation, reactivation, and deactivation before retiring the old table.
5. Keep the old table until verification and rollback are complete. Delete it manually only after confirming it is no longer needed. The legacy export is intentionally retained to avoid breaking existing stack dependencies.

If the old table is empty (as expected after a deliberate data reset), step 2 can be skipped. Do not attempt to alter a DynamoDB table's primary key in place; DynamoDB does not support that operation.

## Slot-key range queries

Queries use `slotKey BETWEEN '<startUTC>#' AND '<endUTC>#~'`. This includes every slot ID at both timestamp boundaries. API handlers continue applying their existing date/time visibility filters, including the exclusive end-time rule.

No NAT Gateway is required or introduced by this change.
