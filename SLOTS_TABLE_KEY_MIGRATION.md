# MediSlot — final single Slots table

## Target architecture

The final deployment has exactly one Slots table:

- Physical table name: `medislot-${Environment}-slots`
- Partition key: `doctorId` (String)
- Sort key: `slotKey` (String), formatted as `startTimeUTC#slotId`
- `slotId` and `startTimeUTC` remain normal attributes.
- No `DoctorStartIndex` GSI.
- All Lambdas import `medislot-${Environment}-SlotInventoryTableName`.
- IAM policies grant access to `medislot-${Environment}-slots`.

The final `Cloudformation/Data_Stack.yaml` is the steady-state template.
`Data_Stack_Transition.yaml` is a one-time compatibility template required because
the currently deployed Application Stack imports the old `SlotInventoryTableV2Name`
CloudFormation export.

## Important: current environment has two physical tables

The supplied deployment screenshot showed both:
- `medislot-dev-slots` (legacy key schema)
- `medislot-dev-slots-v2` (new doctorId/slotKey schema)

DynamoDB does not allow an existing table's primary key to be changed in place.
Do not delete either table until you have checked whether it contains records and
taken any required backup. Do not run the final template directly against the
current stack without following the sequence below.

## One-time deployment sequence (dev example)

1. Put MediSlot into a maintenance window / stop patient booking traffic.
2. Inspect both tables and take on-demand backups or exports if their data may
   be needed. Check Lambda environment variables and CloudFormation stack state.
3. If `medislot-dev-slots-v2` contains records that must be retained, migrate
   them to the replacement table after it is created. For every item, ensure
   `slotKey = startTimeUTC + '#' + slotId`; write using `(doctorId, slotKey)`.
   Do not discard booking-related attributes. The MySQL `appointments.slot_id`
   values continue to refer to the unchanged `slotId` attribute.
4. Delete the legacy physical table `medislot-dev-slots` only after its backup
   and after confirming it is the old-schema table. This name must be free for
   CloudFormation to create the replacement table. Do not delete `slots-v2`.
5. Deploy `Cloudformation/Data_Stack_Transition.yaml` to the existing
   `medislot-dev-data` stack. The stack-managed `slots-v2` resource is replaced
   by a new `medislot-dev-slots` table with the desired key schema. Its
   `UpdateReplacePolicy: Retain` keeps the old `slots-v2` table temporarily.
   The transition template exports both the canonical name and a temporary
   compatibility alias, both pointing to the single new `slots` table.
6. If needed, migrate the records preserved from `slots-v2` into the new
   `medislot-dev-slots` table. Verify counts and sample items before continuing.
7. Deploy `Cloudformation/Security_IAM_Stack.yaml` and then
   `Cloudformation/Application_Stack.yaml`. The application now imports the
   canonical `SlotInventoryTableName` export. The transition alias remains only
   so CloudFormation can safely update the existing cross-stack dependency.
8. Test doctor availability, patient availability, slot materialisation,
   reactivation/deactivation, booking, cancellation, and waitlist behavior.
9. Once the application is confirmed to use `medislot-dev-slots`, delete the
   retained `medislot-dev-slots-v2` table only if its data is no longer needed
   and backups are verified.
10. Deploy the final `Cloudformation/Data_Stack.yaml`. This removes the
    temporary `SlotInventoryTableV2Name` export. The final architecture now
    has one Slots table and no V2 export.

## If both tables are empty

If you have verified that both tables contain zero items and no data needs
preserving, steps 2–3 and 6 can be reduced to backup/empty checks. Still follow
the CloudFormation transition sequence; do not manually delete the stack-managed
`slots-v2` table while CloudFormation believes it owns it.

## Rollback and safety

- Keep backups until all end-to-end checks pass.
- Do not delete the retained `slots-v2` table until the new table and all Lambda
  operations are verified.
- If a step fails, stop and inspect CloudFormation stack events before retrying.
- No NAT Gateway is required or introduced by this change.
