# MediSlot Slots Table Architecture

## Canonical table

MediSlot uses one DynamoDB table for doctor slot inventory:

- Table name: `medislot-${Environment}-slots`
- Partition key: `doctorId` (String)
- Sort key: `slotKey` (String)
- Sort key format: `startTimeUTC#slotId`
- Global secondary indexes: none

The `slotId` and `startTimeUTC` values are regular item attributes. They are not separate key attributes.

## Application configuration

Scheduling Lambda functions receive the table name through the `SLOTS_TABLE` environment variable. Shared slot access code reads this configuration; application code must not hardcode a legacy table name.

## CloudFormation ownership

`Cloudformation/Data_Stack.yaml` owns the canonical table and exports its name as:

`medislot-${Environment}-SlotInventoryTableName`

`Cloudformation/Application_Stack.yaml` configures the application Lambdas to use the canonical table.

`Cloudformation/Security_IAM_Stack.yaml` grants the required application roles access to the canonical table. There is no conditional legacy Slots-table access.

## Deployment workflow

The GitHub Actions workflow deploys the final templates directly:

1. Network Stack
2. Security IAM Stack
3. Data Stack
4. Application Stack
5. Live smoke tests and canonical Slots-table verification

The workflow does not deploy transition templates or run a separate migration-finalization job.

## Operational notes

- Do not recreate legacy Slots tables or grant IAM access to them.
- Do not change the DynamoDB primary key in place. If a future key redesign is required, plan a separate data migration.
- Keep the table name, key schema, Lambda `SLOTS_TABLE` configuration, and IAM resource ARNs consistent.
