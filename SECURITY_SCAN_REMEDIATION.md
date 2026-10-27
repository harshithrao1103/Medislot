# MediSlot Milestone 2 — Checkov remediation notes

Changes in this archive:

- Enabled DynamoDB point-in-time recovery on tables that lacked it.
- Added reserved concurrency (10) to interactive authentication Lambdas.
- Added API Gateway access logging and X-Ray tracing to the authentication stage.
- Did not change RDS properties or KMS keys, to avoid modifying/replacing the existing database.

## Remaining findings and decisions

**This package is not claimed to pass all 59 Checkov findings.** Re-run CI and review the remaining findings. In particular:

- `CKV_AWS_119`, `CKV_AWS_158`, `CKV_AWS_173`: Checkov expects customer-managed KMS keys; the MediSlot specification mandates AWS-managed keys. Do not create customer-managed keys just to pass these checks. Document narrowly scoped suppressions after mentor/security review.
- `CKV_AWS_59`: Public registration/login/refresh endpoints are intentional; require application-level validation, throttling, and abuse protection. Logout should be assessed separately for authorization requirements.
- `CKV_AWS_120`: Do not enable caching on authentication methods returning credentials/tokens.
- `CKV_AWS_116`: API Gateway invokes auth Lambdas synchronously; Lambda async DLQ does not capture those synchronous failures. Handle errors with API responses/logging.
- `CKV_AWS_117`: Token/authorizer/test Lambdas need not be VPC-attached when accessing public AWS service endpoints with IAM authorization; verify security architecture.
- `CKV_AWS_111`: Review individual IAM write statements and restrict where feasible; some EC2 ENI actions require wildcard resources. Avoid blanket suppressions.
- RDS Multi-AZ, enhanced monitoring, IAM DB auth: review availability, cost, and credential design before modifying the deployed instance.
- S3 server-access logging: configure a dedicated logging destination with bucket policy, or document an approved alternative.

## Deployment safety

1. Inspect CloudFormation Change Sets, especially the Data stack. Do not execute if the RDS DB instance is marked `Replacement: True`.
2. Confirm GitHub Actions CI and deployment pass.
3. Confirm registration, login, refresh, logout, and doctor authorization with live API tests in Postman.
4. This ZIP cannot verify the user's AWS environment or GitHub Actions results.
