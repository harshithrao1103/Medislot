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
