# MediSlot — clean single Slots table deployment

## Final architecture

The steady-state deployment contains exactly one Slots table:

- Physical name: `medislot-${Environment}-slots`
- Partition key: `doctorId` (String)
- Sort key: `slotKey` (String)
- `slotKey` format: `startTimeUTC#slotId`
- `slotId` and `startTimeUTC` remain ordinary item attributes.
- No Slots-table GSI is created.
- Doctor API, Patient Domain and Slot Materialiser use the `SLOTS_TABLE` environment variable sourced from the canonical `SlotInventoryTableName` CloudFormation export.
- IAM access is scoped to `medislot-${Environment}-slots`.

## Data-loss decision

This deployment intentionally does not migrate or preserve existing DynamoDB records. The project owner has confirmed that existing Slots records and other DynamoDB data are disposable. The Slots table is recreated with the required composite key.

## Important CloudFormation dependency

The currently deployed Application Stack may still import the old
`SlotInventoryTableV2Name` export, even though this ZIP's Application Stack
uses the canonical `SlotInventoryTableName` export. CloudFormation does not
allow an exported value to change or be removed while another stack imports it.

Do not manually delete both Slots tables before following the sequence below.
The old `medislot-${Environment}-slots` table is the legacy, separately-created
table and must be deleted to free its physical name. The `slots-v2` table is
currently managed by the Data Stack and should be removed by CloudFormation
when the resource is replaced.

## Deployment sequence (dev example)

1. Stop patient booking traffic / put the application into a maintenance window.
2. Deploy `Cloudformation/Application_Stack.yaml` to the existing Application
   Stack first. This changes all three Lambda `SLOTS_TABLE` references from the
   old V2 export to the canonical `SlotInventoryTableName` export.
3. Verify the old export has no importers:

   ```powershell
   aws cloudformation list-imports `
     --export-name medislot-dev-SlotInventoryTableV2Name `
     --region us-east-1
   ```

   The result must show no importing stacks. If it still lists the Application
   Stack, stop and inspect the deployed Application Stack before proceeding.
4. Delete the legacy physical table `medislot-dev-slots` (the old `slotId`
   partition-key table) so the required physical name is free. Do not delete
   `medislot-dev-slots-v2` manually; it is managed by CloudFormation.
5. Deploy `Cloudformation/Data_Stack_Transition.yaml` to the existing Data
   Stack using the same parameter values as its current deployment. This
   replaces the stack-managed `slots-v2` resource with the new physical table
   `medislot-dev-slots`, using `doctorId` + `slotKey`. The update-replacement
   policy is `Delete`, so the old stack-managed `slots-v2` table is removed.
   The transition template temporarily keeps the old V2 export name as a
   compatibility alias to the new table; no stack should import it now.
6. Verify the Data Stack is `UPDATE_COMPLETE`, and verify the physical table:

   ```powershell
   aws dynamodb describe-table `
     --table-name medislot-dev-slots `
     --region us-east-1 `
     --query "Table.{Status:TableStatus,Keys:KeySchema,Indexes:GlobalSecondaryIndexes}"
   ```

   Expected key schema: `doctorId` HASH and `slotKey` RANGE. Expected GSI list:
   empty / absent.
7. Deploy the final `Cloudformation/Data_Stack.yaml` to the same Data Stack.
   This removes the temporary `SlotInventoryTableV2Name` export. The
   Application Stack already imports only the canonical export.
8. Verify the V2 export no longer exists and the canonical export resolves to
   `medislot-dev-slots`. Then deploy/publish the Lambda packages and test slot
   materialisation, doctor availability, patient availability, booking,
   cancellation, and waitlist behavior.
9. Resume traffic only after all checks pass.

## Important notes

- Do not manually delete `medislot-dev-slots-v2`; CloudFormation owns it and
  replaces/deletes it in step 5.
- Other DynamoDB tables are independent of this Slots-table key change. Clear
  their data only if intentionally required; this deployment does not require
  deleting those tables.
- The Data Stack transition/final templates preserve the existing other data
  resources and their settings.
- No NAT Gateway is required or introduced by this change.
- If any stack update fails, stop and inspect CloudFormation stack events
  before retrying. Do not continue to the next step after a failed update.
