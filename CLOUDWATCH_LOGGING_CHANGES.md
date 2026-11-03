# MediSlot CloudWatch logging additions

These changes extend the existing logging configuration; no existing business routes or database tables were intentionally changed.

## CloudFormation
- Application Stack: explicit `/aws/lambda/medislot-${Environment}-db-init` log group with 14-day retention and a dashboard log group `/medislot/${Environment}/doctor-dashboard` with 14-day retention.
- Security IAM Stack: least-privilege EC2 permission to create streams and write events in the dashboard log group.
- Data Stack: RDS MySQL `error` log export to CloudWatch (not slow query logging, which needs additional parameter configuration).
- Application Stack EC2 bootstrap: installs and configures Amazon CloudWatch Agent, collecting `/var/log/medislot-doctor.log` from Gunicorn stdout/stderr.
- Existing Lambda log groups and API Gateway access logs remain configured.

## Application logs
- Shared response helper emits metadata-only JSON `API_RESPONSE` (HTTP status, correlation ID) for auth and doctor API calls; no response bodies or tokens.
- Doctor API emits `DOCTOR_API_REQUEST` and `DOCTOR_API_REJECTED` events with route, method, doctor ID, and correlation ID.
- Authorizer emits `JWT_AUTHORIZATION_ALLOWED` and `JWT_AUTHORIZATION_DENIED` without recording tokens or identities.
- Slot Materialiser emits STARTED, COMPLETED (counts/duration), and FAILED events.
- DB Init emits STARTED and COMPLETED events; existing failure exception logging remains.

## Deployment and operational notes
1. Review the CloudFormation change sets before deploying, especially any changes to `DoctorDashboardInstance` and `MySQLDatabase`. A CloudFormation update to EC2 UserData does **not** automatically rerun bootstrap on an existing instance; the dashboard log forwarding may require a planned instance replacement or a separate SSM-managed installation. Do not replace the existing instance without accounting for sessions and the dashboard endpoint.
2. If the DB Init log group was already created automatically by Lambda, CloudFormation creation can fail with `AlreadyExists`; import the existing group or plan a safe migration before deploying.
3. The RDS `error` log export may take time to appear. Existing CloudWatch logs cannot be confirmed from source code alone.
4. Review logging cost and retention in staging/prod. Do not log request bodies, credentials, JWTs, refresh tokens, or patient records.
5. The current ZIP contains only the components already present in the project; future booking, cancellation, waitlist and notification Lambdas need logging when implemented.
6. The supplied archive's application behavior was preserved, including its existing consultation duration configuration. This logging update does not fix unrelated application bugs.

## Validation
- Python `compileall` passed.
- CloudFormation YAML parsing passed for edited templates.
- Full pytest suite could not run in the local execution environment because the `bcrypt` dependency is missing; the existing GitHub Actions CI gate should install project test dependencies and run all tests.
- CloudFormation deployment and live log delivery have not been tested against AWS.
