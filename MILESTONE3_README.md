# MediSlot Milestone 3 — Doctor Domain

This is an **additive implementation** on top of the supplied Milestone 2 project. Original Milestone 2 authentication Lambda sources, database schema, and network stack are preserved. CloudFormation application, security, and data stacks have new resources or properties; existing auth paths and roles are retained.

## New functionality

- Doctor profile GET/PUT (`users` + `doctors` MySQL tables); creation remains the existing doctor registration API.
- Profile photo pre-signed S3 POST, with MIME and size validation (up to 5 MiB); photo key stored in RDS. S3 bucket remains private.
- Consultation types: fixed Quick (15/1), Standard (30/1), Detailed (60/1), Health Camp (60/2–10), doctor-owned CRUD.
- Weekly recurring availability: `PUT` replaces the doctor's full list, `GET` lists it; weekdays use Python convention Monday=0 to Sunday=6; overlap checks.
- Blackouts: GET/POST/DELETE, stored in MySQL.
- Slot materialiser: EventBridge Scheduler at **00:30 IST daily**; 30-day rolling window; local IST converted to UTC; SHA-256 deterministic slot IDs; DynamoDB conditional PutItem; obsolete unbooked slots marked INACTIVE. No existing booking counters or versions are reset.
- Schedule day view: DynamoDB GSI by doctor and UTC start, joined with RDS patient names for booked appointments.
- Flask first-version doctor dashboard on EC2 with server-side sessions; it is an API-only client, not a database client.

## Doctor endpoints

All endpoints require `Authorization: Bearer <doctor access JWT>`; a patient JWT must be rejected.

| Method | Path | Body / query |
| --- | --- | --- |
| GET | `/doctors/me` | none |
| PUT | `/doctors/me` | subset of `name`, `specialization`, `experience_years`, `qualifications`, `hospital_name`, `clinic_address`, `city`, `languages`, `timezone` |
| POST | `/doctors/me/photo` | `{"contentType":"image/jpeg","sizeBytes":12345}`; returns `upload.url` and multipart `upload.fields` |
| GET/POST | `/doctors/me/consultation-types` | POST `{"name":"Standard Consult","duration_minutes":30,"fee":"300.00","capacity":1}` |
| PUT/DELETE | `/doctors/me/consultation-types/{id}` | PUT full consultation type payload |
| GET/PUT | `/doctors/me/availability` | PUT `{"patterns":[{"consultation_type_id":1,"day_of_week":0,"start_time":"09:00","end_time":"11:00"}]}` |
| GET/POST | `/doctors/me/blackouts` | POST `{"blackout_date":"2026-11-05","reason":"Leave"}` |
| DELETE | `/doctors/me/blackouts/{id}` | none |
| GET | `/doctors/me/schedule?date=2026-11-02` | date in IST |

The existing `/auth/doctors/register` is the **create** profile endpoint. Licence number and email remain immutable through the profile-update API. The doctor ID is always taken from the existing JWT authorizer context.

## Deployment

1. Work in a feature branch; **do not directly push to protected main/master**. Keep the current GitHub Actions OIDC and CloudFormation execution role settings.
2. Review new resources and permissions in `Cloudformation/Security_IAM_Stack.yaml`, `Data_Stack.yaml`, and `Application_Stack.yaml`. CloudFormation stack order remains Network → Security/IAM → Data → Application. No console-created resources.
3. Run `python -m pip install -r tests/requirements.txt`, `python -m pytest -q tests`, `cfn-lint Cloudformation/*.yaml`, and `checkov -d Cloudformation --framework cloudformation` in CI.
4. The existing `deploy.yaml` packages and uploads both new Lambda functions and the doctor dashboard ZIP to the existing artifacts bucket. It then deploys the application stack. The dashboard instance downloads its artifact in UserData and starts Gunicorn on port 80.
5. Inspect CloudFormation output `DoctorDashboardUrl`. Test doctor login, profile editing, availability editing, schedule display, and photo upload.
6. For a controlled idempotency check, invoke `medislot-dev-slot-materialiser` twice with `{"date":"2026-11-02"}`. First run should report created slots; second should report zero newly created and matching `alreadyPresent`. Inspect `medislot-dev-slots` via the `DoctorStartIndex` GSI. Check `bookedCount` and `version` unchanged.

**Important live-deployment caveats:** The included EC2 first version serves **HTTP only**, not HTTPS. Do not use real user credentials or production data until TLS, a proper domain, and secure-cookie settings are configured. AWS account permissions, CloudFormation Change Set results, and EC2 bootstrap must be verified in your AWS environment. Updating an existing EC2 instance's UserData does not rerun it automatically; dashboard releases need an instance replacement or a separate rolling deployment mechanism. The first EC2 bootstrap requires outbound package-repository access.

## Timezone scope deviation

Per the user's explicit scope decision, doctor availability is restricted to `Asia/Kolkata` and stored slots use UTC; **DST-specific handling/test cases and new X-Ray tracing are not implemented**. This differs from the official Milestone 3 mentor checkpoint. Existing Milestone 2 tracing configuration is not removed.

## Source preservation

No existing Milestone 2 auth Lambda implementation is edited, and `database/schema.sql` is unchanged. The changes to existing files are strictly additive infrastructure changes plus Lambda packaging and CI/CD packaging. The network template remains untouched. Do not run `RESET_EXISTING_SCHEMA_DEV_ONLY.sql`.

## Tests and validation

`tests/test_milestone3.py` covers IST→UTC slot times, stable IDs, wrong-role rejection, consultation constraints, and rerun idempotency with a fake table. Actual AWS integration/smoke tests must be run after deployment. Syntax and YAML parsing are checked locally; package dependency installation may require internet access in CI.
