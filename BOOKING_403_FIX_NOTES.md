# MediSlot booking 403 and API deployment fixes

## Changes in this package

1. `doctor_dashboard/app.py`
   - CSRF validation now accepts a token from either a normal form field (`csrf_token`) or the `X-CSRF-Token` request header.
   - Uses `secrets.compare_digest` and returns a JSON error for rejected JSON requests.
   - Passes the route's `doctor_id` to the patient doctor template.

2. `doctor_dashboard/templates/patient_doctor.html`
   - Booking fetch now sends `X-CSRF-Token`.
   - Sends the route `doctor_id` instead of reading `doctor.user_id`, which may be absent from the public doctor response.

3. `Cloudformation/Application_Stack.yaml`
   - Adds `ApiDeploymentVersion`, separate from `CodeVersion`.
   - Uses that parameter in the doctor-domain API Gateway deployment description. Changing it forces CloudFormation to create a fresh API Gateway deployment snapshot without changing Lambda package keys.

4. `.github/workflows/deploy.yaml`
   - Normal deployments now set `ApiDeploymentVersion` to the commit SHA.

5. `.github/workflows/deploy-api.yaml`
   - Adds a manually triggered API-only deployment workflow.
   - Reads the currently deployed `CodeVersion` from the existing stack and keeps it unchanged.
   - Does not package or upload Lambda code, overwrite the JWT signing secret, update the EC2 dashboard, or modify network/data stacks.
   - It updates the existing application CloudFormation stack so its API deployment snapshot and `dev` stage can include the appointments methods already declared in the template.

## Validation performed

- `doctor_dashboard/app.py` passed Python syntax compilation.
- `tests/test_patient_slot_display.py`: 3 passed.
- Full pytest collection could not complete in this environment because dependencies `bcrypt` and `pymysql` are not installed.
- Live AWS deployment and a real patient booking were not run from this environment.

## Important deployment note

The API-only workflow is manual and requires the `confirm_deploy` checkbox to be checked. Checking it authorizes the CloudFormation update to the live application stack. After the workflow completes, verify the deployed `dev` stage includes `POST /appointments`, then test a booking in the browser. A remaining AWS Authorization-header error, if it persists after the route deployment, needs the exact API Gateway/Lambda response and CloudWatch logs to diagnose; this ZIP alone cannot prove that live AWS error is resolved.
