# MediSlot database and authentication update

## Final database design

RDS MySQL (8): `users`, `doctors`, `patients`, `consultation_types`, `doctor_availability`, `doctor_blackouts`, `appointments`, `refund_ledger`.

DynamoDB (6): `slots`, `idempotency`, `refresh-tokens`, `revoked-jwts`, `waitlist`, `notification-dispatch`.

The existing `Data_Stack.yaml` already provisions all six finalized DynamoDB tables with the agreed primary keys. DynamoDB is schemaless: non-key attributes (such as `bookedCount`, `version`, and `createdAt`) are stored on items by application Lambdas; they do not belong in CloudFormation `AttributeDefinitions` unless used as keys or indexes.

## Existing RDS database: important migration warning

The uploaded project has a six-table legacy schema. Its `patients` and `doctors` tables use independent `patient_id`/`doctor_id` keys; the new schema uses `user_id` as both PK and FK. Existing appointments also use different column names. `CREATE TABLE IF NOT EXISTS` will not migrate these structures. The `db_init` Lambda now detects mismatched existing tables and refuses to continue rather than silently leaving broken schemas. **No AWS database has been deleted or changed by this ZIP.**

For a disposable development environment only: take an RDS snapshot/backup, review `database/RESET_EXISTING_SCHEMA_DEV_ONLY.sql`, execute it manually with appropriate database permissions, then invoke the packaged `db_init` Lambda. This destroys existing records. For any environment with data to keep, create a reviewed migration that maps old IDs and preserves records instead. Never automatically run the reset file in CI/CD.

## Registration (single MySQL transaction)

`POST /auth/doctors/register`: `name`, `email`, `password`, `licence_number`, `specialization`, `experience_years` (integer), `qualifications`, `hospital_name`, `clinic_address`, `city`, `languages` (nonempty array), `timezone` (IANA), optional `photo_key`.

Example JSON:
```json
{
  "name": "Doctor Example", "email": "doctor@example.com", "password": "ExamplePassword123",
  "licence_number": "LIC-1001", "specialization": "Cardiology", "experience_years": 8,
  "qualifications": "MBBS, MD", "hospital_name": "Example Clinic",
  "clinic_address": "Main Road", "city": "Hyderabad",
  "languages": ["English", "Telugu"], "timezone": "Asia/Kolkata", "photo_key": null
}
```

`POST /auth/patients/register`: `name`, `email`, `password`, `date_of_birth` (`YYYY-MM-DD`), `gender`, `phone`, `city`, `emergency_contact`, optional `notes`.

Example JSON:
```json
{
  "name": "Patient Example", "email": "patient@example.com", "password": "ExamplePassword123",
  "date_of_birth": "2000-05-12", "gender": "MALE", "phone": "9999999999",
  "city": "Hyderabad", "emergency_contact": "8888888888", "notes": null
}
```

The registration Lambda writes `users` and the relevant profile row in one transaction, committing both or rolling back both. Existing login routes and role checks are preserved.

## Refresh token (fixed 7-day lifetime, no rotation)

Login still issues a new access token and a new opaque refresh token. The refresh token is stored as a SHA-256 hash in DynamoDB, with a fixed expiry 7 days after login (CloudFormation `parameters.json` already sets `RefreshTokenDays` to `7`).

`POST /auth/refresh` with `{"refreshToken":"<existing token>"}` now verifies the stored token and its `expiresAt`, then returns only a **new access token**, `expiresAt`, and the unchanged `refreshExpiresAt`. It does not delete/rotate the refresh token or extend its expiry. After 7 days the user must log in again. Logout revokes the presented refresh token and access-token `jti` as before.

Security trade-off: without refresh-token rotation, a stolen refresh token may be replayed until it expires or is revoked. The token must be kept secret and sent over HTTPS. DynamoDB TTL deletion is asynchronous; expiry is checked in code.

## Deployment

The existing GitHub Actions workflow packages Lambda ZIPs and deploys CloudFormation. Commit and deploy the updated code through that workflow. RDS table changes are **not** made by a CloudFormation template directly; the packaged `db_init` Lambda applies `database/schema.sql` when invoked. Back up and migrate/reset the old schema **before** invoking it. No live AWS deployment or database deletion was performed here.

## Current scope

The uploaded ZIP contains authentication and database initialization Lambdas, not the future booking/availability/cancellation application implementations. The new RDS schema establishes their target tables, but does not implement those future APIs. All 14 agreed tables are represented by the schema and CloudFormation.
