# MediSlot Milestone 2 — Implementation and deployment notes

## Scope
This package extends the original project with six authentication Lambdas, seven API Gateway methods, a JWT Lambda Authorizer, tests, and a CI/CD gate. The existing database-init Lambda and four stacks are retained. **No Milestone 3+ business APIs are implemented.**

## Required configuration before deployment

1. GitHub Actions secret `AWS_ROLE_ARN` must be the existing OIDC role.
2. Add GitHub Actions secret `JWT_SIGNING_SECRET` with a randomly generated string **at least 32 characters**. Never commit this value. Deployment writes it to `/medislot/<environment>/auth/jwt-signing-secret` using `ssm:PutParameter` as `SecureString`. Ensure the GitHub OIDC role has `ssm:PutParameter` permission scoped to this path. A missing permission will fail deployment.
3. Ensure the CloudFormation execution role can create `AWS::ApiGateway::*`, `AWS::Lambda::Permission`, `AWS::Logs::LogGroup` and the new Lambda functions; and can pass the execution roles.
4. Protect the actual default branch **`main`** (the brief says `master`): require PR, one peer approval, and the **`Validate and Test`** status check from `Milestone 2 Required CI`. Enforce up-to-date branches and disallow bypass. GitHub branch protection is an account setting and cannot be changed by adding files to the ZIP.
5. Deploy via a feature branch -> reviewed PR -> merge. Do not run CloudFormation by hand. `deploy.yaml` triggers on push to `main`, packages and uploads all Lambda ZIPs, then deploys through `aws cloudformation deploy` (CloudFormation Change Sets).

## API methods

| Method | Path | Authentication |
| --- | --- | --- |
| POST | `/auth/doctors/register` | Public |
| POST | `/auth/patients/register` | Public |
| POST | `/auth/doctors/login` | Public |
| POST | `/auth/patients/login` | Public |
| POST | `/auth/refresh` | Opaque refresh token in JSON body |
| POST | `/auth/logout` | Bearer access token + refresh token in JSON body; handler validates token |
| GET | `/doctor-auth-check` | Custom authorizer; doctor only |

Registration JSON: `{"name":"Example","email":"example@example.org","password":"StrongPass123","specialization":"General Medicine"}` (specialization required for doctors only). Login JSON: `{"email":"...","password":"..."}`. Refresh/logout JSON: `{"refreshToken":"..."}`. Do not print or persist access/refresh tokens in CI logs.

## Authentication rules

- bcrypt >=12, password >=10 characters with a letter and digit; bcrypt's 72-byte limit enforced.
- Global email uniqueness via `users.email` UNIQUE; RDS `users.role` stores uppercase role, JWT uses lowercase role.
- JWT HS256, exactly five claims: `sub`, `role`, `iat`, `exp`, `jti`. 15-minute default from SSM.
- Refresh tokens: 7 days by default, 48 random bytes, SHA-256 token hash used as DynamoDB partition key, TTL attribute `expiresAt`.
- Refresh rotation consumes the previous token with a conditional delete to prevent simultaneous replay.
- Logout deny-lists JWT `jti` until access token expiry and removes the supplied refresh token.
- Authorizer cache is disabled (`AuthorizerResultTtlInSeconds: 0`) so revocation is effective immediately. JWT signature/expiry/revocation are checked by the authorizer; role check on doctor endpoint returns HTTP 403.
- Secret read uses SSM `SecureString`, cached in module scope at cold start. It is never stored as a literal in code or Lambda environment variables.

**Specification conflict:** The project PDF demands the JWT payload contain *exactly* five claims, yet also mentions issuer verification. This implementation follows the exact-five-claims rule, so it cannot validate an `iss` claim that does not exist. Confirm this contradiction with your mentor before the final review.

## Tests

- CI: `python -m pytest -q tests`, Python compilation, `cfn-lint`, `checkov`, and `gitleaks`.
- Deploy workflow: database initialization and live HTTP smoke tests after stack deployment (registration, login, role check, tamper, refresh rotation, logout revocation).
- Expired JWT rejection is unit-tested with an explicitly expired token; live expiration requires an additional controlled test.
- Manual mentor evidence still required: approved PR and protected-branch settings, CloudFormation run logs, password hashes in RDS, VPC DB connectivity and security group audit.

## Known infrastructure exception

The uploaded existing Data Stack specifies a **customer-managed KMS key for RDS storage** and the Security IAM Stack references a **customer-managed key for the RDS secret**. The PDF prohibits customer-managed KMS keys. These were intentionally *not* removed in this package because changing encryption on a deployed RDS instance can require replacement or migration. Plan a controlled, backed-up migration to AWS-managed keys; do not delete or replace the running database without a migration plan. This remains a PDF compliance gap.

## Local validation limits

A ZIP cannot prove successful deployment. The real AWS stack update, GitHub status checks, external Python dependency installation, secret provisioning permissions, and live HTTP tests must run in your account. Check the GitHub Actions logs and CloudFormation stack events before declaring Milestone 2 complete.


## Milestone 2 verification and known blockers (read before merge)

This source package is **not evidence of successful deployment**. The following must be verified in your AWS account:

1. GitHub branch protection, required CI status check, and one peer-approved PR on the actual default branch. The PDF says `master`; this repository currently uses `main`. Agree the branch name with your reviewer, then change both workflows and repository protection together if required.
2. GitHub Actions must pass lint, unit tests, secret scanning, IaC scanning, CloudFormation deployments, DB initialization and live API smoke tests. Check the workflow run logs.
3. Verify an expired access token returns 401 against API Gateway (the live smoke script tests tampering, role separation, refresh replay and logout; the offline unit test tests expiry).
4. Verify database user passwords are bcrypt hashes with cost >= 12 and test RDS connectivity from the VPC Lambda.
5. The existing RDS uses a **customer-managed KMS key**, contrary to the PDF's AWS-managed-key-only rule. Changing an existing RDS storage encryption key can require snapshot migration / replacement and must NOT be applied blindly to a running database. This package intentionally retains the deployed setting; plan a controlled migration and reviewer sign-off.
6. The PDF simultaneously demands JWT issuer validation and an access-token payload containing **exactly** `sub`, `role`, `iat`, `exp`, `jti`. The implementation honors the exactly-five-claims rule and does not claim issuer validation. Resolve the contradictory requirement with the reviewer before sign-off.
7. CloudFormation's API Gateway Deployment resource may not create a new deployment on every route/method update; if you add routes later, ensure the deployment is replaced through a CloudFormation change.

Do not label Milestone 2 complete until these checks have passed.
