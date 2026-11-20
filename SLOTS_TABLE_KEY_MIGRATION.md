# MediSlot — final single Slots table deployment

## Final state

The final deployment has exactly one Slots table:

- Physical table: `medislot-${Environment}-slots`
- Partition key: `doctorId` (String)
- Sort key: `slotKey` (String)
- `slotKey` value: `startTimeUTC#slotId`
- `slotId` and `startTimeUTC` remain ordinary item attributes.
- No Slots-table GSI.
- Doctor API, Patient Domain, and Slot Materialiser read `SLOTS_TABLE`.
- The final Application Stack obtains that value from the canonical
  `SlotInventoryTableName` CloudFormation export.
- IAM policies scope Slots access to `medislot-${Environment}-slots`.
- No NAT Gateway is added.

## Data handling

The project owner has confirmed that existing DynamoDB data is disposable.
This deployment does not migrate old Slots records or preserve other
DynamoDB table contents. Do not delete unrelated tables unless you
intentionally want to reset those tables too.

## Why two Application templates are included

CloudFormation currently has an export dependency from the deployed
Application Stack to `SlotInventoryTableV2Name`. The transition template
temporarily uses the existing `medislot-${Environment}-slots-v2` physical
table name directly as a string, removing the cross-stack import. After
the Data Stack creates the final table and removes the V2 export, the final
Application Stack template switches all three Lambda environment variables
to the canonical `SlotInventoryTableName` export.

- `Application_Stack_Transition.yaml`: temporary first deployment only.
- `Application_Stack.yaml`: final steady-state template. Do not replace it
  with the transition template after the migration is complete.

The GitHub Actions workflow performs this sequence:
1. Deploy Network and Security/IAM stacks, temporarily granting the two
   scheduling Lambdas access to both the canonical and legacy table names.
2. Deploy the transition Application Stack, which drops the old V2 export
   import and temporarily points the scheduling Lambdas at `slots-v2`.
3. Deploy `Data_Stack_Transition.yaml`, creating the canonical table and
   retaining a temporary V2 compatibility export.
4. Verify the canonical table schema and export, then deploy the final
   `Application_Stack.yaml` so all three Lambdas use the canonical export.
5. Deploy `Data_Stack.yaml` to remove the temporary V2 export.
6. Redeploy the Security/IAM stack with legacy access disabled.

## Required pre-deployment action

The existing legacy physical table `medislot-${Environment}-slots` occupies
the final table name. It is not the Data Stack's currently managed V2 table.
After confirming the old table is the legacy table with the old `slotId`
partition key, delete **only that legacy table** before starting the workflow.

For the current dev environment:

```powershell
aws dynamodb describe-table `
  --table-name medislot-dev-slots `
  --region us-east-1 `
  --query "Table.{Status:TableStatus,Keys:KeySchema}" `
  --output table `
  --no-cli-pager
```

If it shows the legacy `slotId` HASH key, delete it:

```powershell
aws dynamodb delete-table `
  --table-name medislot-dev-slots `
  --region us-east-1 `
  --no-cli-pager

aws dynamodb wait table-not-exists `
  --table-name medislot-dev-slots `
  --region us-east-1
```

Do **not** manually delete `medislot-dev-slots-v2`. It is the Data Stack's
CloudFormation-managed table. The Data Stack update replaces that resource
with the final `medislot-dev-slots` table.

If the old `medislot-dev-slots` table is already absent, skip the delete
commands. Do not proceed if its key schema is not the expected legacy schema;
inspect the live stack resources and events first.

## Deployment and verification

1. Put MediSlot into a maintenance window. The transition Application Stack
   temporarily points Lambdas at `slots-v2`, and the Data Stack then replaces
   that table. Do not serve booking traffic during the transition.
2. Confirm the old legacy `medislot-dev-slots` table is deleted (or absent).
3. Push this ZIP's project contents to the repository and run the GitHub
   Actions deployment workflow.
4. The workflow deploys `Application_Stack_Transition.yaml`, removing the
   Application Stack's import of `SlotInventoryTableV2Name`.
5. The workflow checks the existing canonical table schema. If the old
   `slotId` HASH table is still present, it stops with an explicit message;
   delete only that confirmed legacy table and rerun the workflow.
6. The workflow deploys `Data_Stack_Transition.yaml`, which creates the
   canonical `slots` table with `doctorId` + `slotKey` and temporarily keeps
   the V2 compatibility export.
7. It verifies the table/export, deploys `Application_Stack.yaml`, then
   deploys `Data_Stack.yaml` to remove the compatibility export and disables
   the temporary `slots-v2` IAM policies.
8. Confirm the workflow finishes with all jobs successful.

Expected final table verification:

```powershell
aws dynamodb describe-table `
  --table-name medislot-dev-slots `
  --region us-east-1 `
  --query "Table.{Status:TableStatus,Keys:KeySchema,GSIs:GlobalSecondaryIndexes}" `
  --output json `
  --no-cli-pager
```

Expected key schema:
- `doctorId` — HASH
- `slotKey` — RANGE

Expected GSI list: empty / absent.

Verify the V2 export has no longer been published:

```powershell
aws cloudformation list-exports `
  --region us-east-1 `
  --query "Exports[?Name=='medislot-dev-SlotInventoryTableV2Name']" `
  --output json `
  --no-cli-pager
```

Expected result: `[]`.

## Stop conditions

- If any stack update fails, stop and inspect its CloudFormation events.
- Do not manually delete `slots-v2`; CloudFormation owns it.
- Do not deploy `Application_Stack.yaml` before the Data Stack has created the
  final table and canonical export.
- If the workflow stops at the Slots schema preflight, inspect the table and
  stack resources before deleting anything. Only the confirmed legacy
  `medislot-dev-slots` table may be deleted for this migration.
- Do not resume traffic until the final Application Stack deployment and
  slot/booking smoke tests pass.
