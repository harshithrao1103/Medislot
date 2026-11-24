# MediSlot booking 403 – root cause and fix

## Symptom
Clicking **Book appointment** shows `Booking failed (403)` with:

    Invalid key=value pair (missing equal-sign) in Authorization header ...

## What the error really means
That text is an **API Gateway** message, not an application error. The request never reached
the JWT authorizer or the Appointment Lambda. API Gateway returns it when a request carrying
`Authorization: Bearer <jwt>` is sent to a path/method that does **not exist on the deployed stage**
(it tries to parse the header as an AWS SigV4 signature, fails, and answers 403).

## Root cause
`deployed-api.json` (export of the live `dev` stage) contains no `/appointments` path at all.
The Milestone 5 resources/methods (`AppointmentsPost`, etc.) exist in `Application_Stack.yaml`,
but the stage kept pointing at an **old API Gateway deployment snapshot**.

The earlier attempt put `ApiDeploymentVersion` into the Deployment `Description`. That does NOT
create a new snapshot: CloudFormation only edits the description of the existing deployment.
A new snapshot is only created when the Deployment resource's **logical ID changes**.

## Fixes in this package
1. `Cloudformation/Application_Stack.yaml`
   - Renamed `DoctorDomainDeploymentMilestone4V1` -> `DoctorDomainDeploymentMilestone5V1`
     (and the `AuthStage.DeploymentId` reference). CloudFormation now creates a fresh deployment
     containing `POST /appointments`, repoints the `dev` stage, then removes the old deployment.
   - Rule going forward: rename this logical ID (e.g. `...Milestone5V2`) whenever API methods or
     resources are added/changed.
2. `.github/workflows/deploy-api.yaml`
   - Fixed 25 shell line-continuations written as `\\` instead of `\`, which broke every multi-line
     `aws` command in that workflow.
3. Earlier fixes retained: CSRF accepts `X-CSRF-Token`, booking fetch sends it, `doctor_id` passed to template.

## How to deploy
Either run the normal **deploy** workflow (push to main), or run **Publish MediSlot API Gateway Routes**
(manual, tick `confirm_deploy`).

## Verify
- API Gateway console -> `medislot-dev-auth-api` -> stage `dev` -> `/appointments` should show `POST`, `GET`.
- Or: `aws apigateway get-export --rest-api-id 6z22fm70cf --stage-name dev --export-type oas30 out.json`
  and confirm `"/appointments"` is present.
- Then book a slot in the browser. A remaining error would now come from the authorizer/Lambda
  (visible in CloudWatch logs `/aws/lambda/medislot-dev-appointment-api`), not API Gateway.

## Not verified here
No live AWS deployment or real booking could be run from this environment. Templates/workflows were
YAML-validated only. Also make sure the Milestone 5 DB tables exist (`appointments`, `refund_ledger`,
`event_outbox`); `database/schema.sql` / `MIGRATE_MILESTONE5_BOOKING.sql` create them.

---

# Follow-up: booking now returns 500 `{"error":"Internal server error"}`

The 403 is gone (the `/appointments` route is now deployed), so the request reaches the
Appointment Lambda. That JSON is the Lambda's catch-all for an unexpected exception.

## Most likely cause (found in code review)
Milestone 5 needs new columns on `appointments` (`idempotency_key`, `cancelled_at`,
`cancellation_actor`, `active_booking_guard`) and the `event_outbox` table. Nothing in the
pipeline applied them to an existing RDS database:
- `schema.sql` uses `CREATE TABLE IF NOT EXISTS`, which never alters an old `appointments` table.
- `MIGRATE_MILESTONE5_BOOKING.sql` is a manual one-off step.
- `db_init` also rejected the new column layout in its safety check.
The booking `INSERT INTO appointments (..., idempotency_key)` therefore fails ("Unknown column"),
the DynamoDB seat is released, and the Lambda answers 500.

## Fix
`lambdas/db_init/db_init_lambda.py` now:
- accepts both the legacy and the Milestone 5 `appointments` layout in its safety check, and
- runs `migrate_milestone5()` – idempotent (checks each column/index first) – adding the missing
  columns, `uq_appointment_idempotency`, `uq_active_patient_slot` and `uq_refund_appointment`.
`deploy.yaml` already invokes db-init on every deploy, so a normal deploy applies it. No manual SQL needed.
(Logic unit-tested with a fake cursor: legacy table, re-run, already-migrated. Not run against real MySQL.)
If an index fails to add because of pre-existing duplicate active bookings, db-init returns the
error text in its response and the deploy step fails; clean the duplicates and redeploy.

## Confirm the cause (if 500 persists)
CloudWatch Logs -> `/aws/lambda/medislot-dev-appointment-api` -> look for "Appointment API failure"
and its traceback (the response header `X-Correlation-ID` matches the log line).
