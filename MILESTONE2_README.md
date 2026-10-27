# MediSlot Milestone 2 — Implementation and Deployment Notes

## Scope

This package extends the original project with six authentication Lambdas, seven API Gateway methods, a JWT Lambda Authorizer, tests, and a CI/CD gate.

The existing database-init Lambda and four CloudFormation stacks are retained.

**No Milestone 3+ business APIs are implemented.**

## Required Configuration Before Deployment

1. GitHub Actions secret `AWS_ROLE_ARN` must be the existing OIDC role.

2. Add GitHub Actions secret `JWT_SIGNING_SECRET` with a randomly generated string **at least 32 characters long**. Never commit this value. Deployment writes it to `/medislot/<environment>/auth/jwt-signing-secret` using `ssm:PutParameter` as `SecureString`. Ensure the GitHub OIDC role has `ssm:PutParameter` permission scoped to this path. Missing permission will fail deployment.

3. Ensure the CloudFormation execution role can create `AWS::ApiGateway::*`, `AWS::Lambda::Permission`, `AWS::Logs::LogGroup`, and the new Lambda functions, and can pass the execution roles.

4. Protect the actual default branch **`main`** (the brief says `master`): require pull requests, one peer approval, and the **Validate and Test** status check from **Milestone 2 Required CI**. Enforce up-to-date branches and disallow bypass. GitHub branch protection is an account setting and cannot be changed by adding files to the ZIP.

5. Deploy via a feature branch → reviewed pull request → merge into `main`. Do not run CloudFormation manually outside the approved deployment workflow. The `deploy.yaml` workflow packages and uploads the Lambda ZIPs and deploys using `aws cloudformation deploy` (CloudFormation Change Sets). The workflow can also be started using `workflow_dispatch` if configured.

## API Methods

| Method | Path | Authentication |
|---|---|---|
| POST | `/auth/doctors/register` | Public |
| POST | `/auth/patients/register` | Public |
| POST | `/auth/doctors/login` | Public |
| POST | `/auth/patients/login` | Public |
| POST | `/auth/refresh` | Opaque refresh token in JSON body |
| POST | `/auth/logout` | Bearer access token + refresh token in JSON body |
| GET | `/doctor-auth-check` | Custom authorizer; doctor only |

### Registration JSON

```json
{
  "name": "Example",
  "email": "example@example.org",
  "password": "<YOUR_TEST_PASSWORD>",
  "specialization": "General Medicine"
}
```

**Note:** Replace `<YOUR_TEST_PASSWORD>` with a test password meeting the password policy. The `specialization` field is required for doctors only.

### Login JSON

```json
{
  "email": "example@example.org",
  "password": "<YOUR_TEST_PASSWORD>"
}
```

### Refresh / Logout JSON

```json
{
  "refreshToken": "<YOUR_REFRESH_TOKEN>"
}
```

For logout, also provide the bearer access token in the `Authorization` header.

Do not print or persist access/refresh tokens in CI logs.

## Authentication Rules

- **Password hashing:** bcrypt cost ≥ 12.
- **Password validation:** Minimum 10 characters, including at least one letter and one digit. Enforce bcrypt's 72-byte limit.
- **Email uniqueness:** Global uniqueness through `users.email` UNIQUE.
- **Roles:** RDS `users.role` stores uppercase roles; JWT uses lowercase roles.
- **JWT algorithm:** HS256.
- **JWT payload:** Exactly five claims: `sub`, `role`, `iat`, `exp`, and `jti`.
- **Access token expiry:** 15 minutes by default, configured through SSM.
- **Refresh token expiry:** Seven days by default.
- **Refresh token generation:** 48 random bytes.
- **Refresh token storage:** SHA-256 token hash stored as the DynamoDB partition key, with TTL attribute `expiresAt`.
- **Refresh token rotation:** Consumes the previous token using a conditional delete to prevent simultaneous replay.
- **Logout:** Deny-lists the JWT `jti` until access token expiry and removes the supplied refresh token.
- **Authorizer caching:** Disabled using `AuthorizerResultTtlInSeconds: 0`, ensuring revocation checks occur for each authorized request.
- **JWT verification:** Signature, expiration, and revocation are checked by the authorizer.
- **Doctor authorization:** The doctor-only endpoint returns HTTP 403 for an authenticated user without the required role.
- **Secret management:** JWT signing material is read from SSM `SecureString` and cached in Lambda module scope. It is never hardcoded or stored as a literal in Lambda environment variables.

### Specification Conflict

The project PDF requires the JWT payload to contain **exactly five claims**, but also mentions issuer verification.

This implementation follows the exact-five-claims requirement and therefore cannot validate an `iss` claim that does not exist.

**Confirm this contradiction with your mentor before the final review.**

## Tests

### CI Validation

The **Milestone 2 Required CI** workflow runs:

- Python compilation.
- Unit tests using `python -m pytest -q tests`.
- CloudFormation validation using `cfn-lint`.
- Infrastructure security scanning using Checkov.
- Secret scanning using Gitleaks.

### Deployment Verification

The deployment workflow is intended to perform database initialization and live HTTP smoke tests after successful CloudFormation deployment.

The smoke tests cover:

1. Doctor and patient registration.
2. Doctor and patient login.
3. Role-based authorization.
4. Tampered JWT rejection.
5. Refresh token rotation.
6. Refresh token replay rejection.
7. Logout and token revocation.

Expired JWT rejection is unit-tested using an explicitly expired token. Live expiration requires an additional controlled test.

### Manual Mentor Evidence

The following evidence must still be collected:

- Approved pull request.
- Protected-branch settings.
- Successful CloudFormation deployment logs.
- Password hashes in RDS.
- VPC-to-RDS connectivity.
- Security group configuration and audit.
- Successful live API smoke tests.

## Gitleaks Documentation Finding

An earlier Git commit contained a fictional password in the registration JSON example in this README.

Gitleaks identified that value using the `generic-api-key` rule.

The example has now been replaced with `<YOUR_TEST_PASSWORD>`.

**Important:** Updating this README does not remove the original value from Git history.

If the original value is confirmed to be fictional and has never been used as a real credential, use a narrowly scoped `.gitleaks.toml` exception for that specific historical finding.

Do not disable Gitleaks globally.

If a real credential was exposed, rotate it and remediate the exposure instead of allowlisting it.

## Known Infrastructure Exception

The existing Data Stack specifies a **customer-managed KMS key for RDS storage**, and the Security IAM Stack references a **customer-managed KMS key for the RDS secret**.

The project PDF prohibits customer-managed KMS keys.

These were intentionally not removed because changing encryption on an existing deployed RDS instance may require database replacement or migration.

A controlled migration should include:

1. Database backup.
2. Snapshot verification.
3. Migration planning.
4. CloudFormation Change Set review.
5. Reviewer approval.
6. Controlled deployment and verification.

**Do not delete or replace the running database without an approved migration plan.**

This remains a PDF compliance gap.

## Local Validation Limits

A ZIP file or successful local validation cannot prove that AWS deployment succeeded.

The following must be verified in the actual AWS account:

- GitHub Actions workflow results.
- CloudFormation stack updates.
- IAM and OIDC permissions.
- External Python dependency installation.
- Secret provisioning permissions.
- Database initialization.
- Live HTTP smoke tests.

Check the GitHub Actions logs and CloudFormation stack events before declaring Milestone 2 complete.

## Milestone 2 Verification and Known Blockers

**Read before final submission or deployment.**

This source package is not evidence of successful deployment.

The following items must be verified:

1. **GitHub branch protection:** Confirm the required CI status check and one peer-approved pull request on the default branch. The project PDF specifies `master`, while this repository uses `main`. Confirm the accepted branch name with your reviewer.

2. **GitHub Actions:** Confirm lint, unit tests, secret scanning, infrastructure scanning, CloudFormation deployment, database initialization, and live API smoke tests all pass.

3. **JWT expiration:** Verify that an expired access token returns HTTP 401 through API Gateway.

4. **Password security:** Verify that database user passwords are bcrypt hashes with cost ≥ 12.

5. **Database connectivity:** Verify RDS connectivity from the Lambda functions configured inside the VPC.

6. **KMS compliance:** The existing RDS uses a customer-managed KMS key, contrary to the PDF's AWS-managed-key-only requirement. Any migration must be planned and approved.

7. **JWT issuer conflict:** The PDF requires issuer validation while also requiring exactly five JWT claims. Resolve this contradiction with the reviewer before sign-off.

8. **API Gateway deployment:** CloudFormation's API Gateway Deployment resource may not automatically create a new deployment for every route or method update. Ensure deployment replacement occurs when necessary.

9. **Security scanning exceptions:** Review documented Checkov and Gitleaks exceptions. A passing scan with exceptions does not prove that all underlying risks have been eliminated.

10. **CloudFormation Change Sets:** Review proposed resource modifications and confirm that no unintended RDS replacement or deletion will occur.

## Final Milestone 2 Status

Milestone 2 is ready for final verification only when:

- CI validation passes.
- Gitleaks completes successfully.
- CloudFormation deployment succeeds.
- Database initialization succeeds.
- Live authentication smoke tests pass.
- Required GitHub branch protection is configured.
- Outstanding specification conflicts and infrastructure exceptions are reviewed.

**Do not mark Milestone 2 complete until all required deployment and verification checks have passed.**