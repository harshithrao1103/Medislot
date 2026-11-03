# MediSlot engineering review — 2026-11-03

## Changes made

1. `lambdas/doctor_api/requirements.txt`: added `bcrypt`, `PyJWT`, and `cryptography`, because the bundled `auth_common.py` imports these modules during Lambda initialization.
2. `lambdas/slot_materialiser/requirements.txt`: added the same shared-module dependencies to avoid the same cold-start import failure in the materialiser.
3. `lambdas/_shared/doctor_common.py`: fixed response serialization. `respond()` now creates a JSON-safe base response before serializing MySQL `Decimal`, date/time, and timedelta values with the existing `serialize()` helper.
4. `doctor_dashboard/app.py`: more useful API errors, handling non-JSON errors and malformed login responses, input validation, and ISO-date validation for the schedule query.
5. `doctor_dashboard/templates/base.html` and `doctor_dashboard/static/style.css`: responsive sidebar, active navigation, redesigned top bar, improved forms, tables, alerts and focus styles. Existing Flask routes and API contracts retained.
6. `Cloudformation/Application_Stack.yaml`: runs Gunicorn as an unprivileged `medislot` service account with only `CAP_NET_BIND_SERVICE` for port 80. Session directory ownership is set to the service user.

## Tests performed

- Python compilation and AST parsing: passed.
- CloudFormation YAML syntax parsing with a loader supporting intrinsic tags: passed. This is **not** a full CloudFormation/cfn-lint validation.
- GitHub Actions deployment YAML syntax parsing: passed.
- Targeted response serialization test with `Decimal` and `datetime.time`: passed.
- Full pytest collection: **blocked** because the test environment lacks `bcrypt` (`ModuleNotFoundError`). No full test-suite success is claimed.
- Live AWS deployment, IAM effective permissions, RDS connectivity, browser end-to-end functionality: **not tested**; require the user's AWS account and live endpoints.

## Before deploying

1. Review the changed files and run the existing GitHub Actions CI and deploy workflows; **do not bypass failed tests or scanners**.
2. The current dashboard still uses HTTP on port 80, including login. This is **not appropriate for real credentials or production**. Add TLS/HTTPS through an ALB or reverse proxy with a certificate, and set `COOKIE_SECURE=true` after TLS is in place. Limit `DashboardIngressCidr` to a trusted public IPv4 /32; `0.0.0.0/0` is risky.
3. Verify that the configured AMI includes `dnf`, Python 3 and supports `useradd`; CloudFormation UserData runs only on instance initialization, so an update may need EC2 instance replacement to apply service changes.
4. Review S3/IAM access and permissions in the AWS account; source templates alone cannot prove deployed effective permissions.
5. Verify the doctor profile and consultations APIs after deploy. Check for unexpected existing consultation records before repeating a create action.
6. Do not distribute Git history, local caches, credentials, or environment secrets in deployment ZIPs. This delivery excludes `.git`, caches, and bytecode.

## Verification command (PowerShell)

`aws logs tail /aws/lambda/medislot-dev-doctor-api --since 15m --region us-east-1`

## Limitations

This is a source-code remediation and UI upgrade, not a verified production release. Other runtime issues may surface after deployment, and security scanning plus end-to-end tests remain necessary.
