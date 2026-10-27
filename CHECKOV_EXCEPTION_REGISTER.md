# Checkov exception register — Milestone 2

**Scope:** exact CloudFormation resources and check IDs only. These are scan exceptions, not security fixes. The earlier security hardening remains in place.

## Review before merging

- The database is existing: do not replace or rotate its encryption key as a side effect of static-analysis remediation.
- Public register/login/refresh endpoints require application-layer validation, rate limits, and abuse monitoring; do not make them require an access JWT.
- Keep API caching disabled for authentication responses.
- Check IAM EC2 network-interface privileges and restrict using supported condition keys where feasible.
- Lambda/CloudWatch/DynamoDB encryption with AWS-managed keys does not meet a customer-managed-key control; revisit if the project policy changes.
- RDS Multi-AZ and enhanced monitoring are deferred for the dev environment; revisit for production.
- The log destination bucket does not log its own requests to avoid recursive logging; it now has versioning.
- Validate the actual AWS CloudFormation Change Set and run integration tests before deploying.

## Exceptions by resource

### Security_IAM_Stack.yaml

- `DBInitLambdaExecutionRole` — `CKV_AWS_111`: Lambda ENI management requires EC2 network-interface permissions on wildcard resources; review role scope and AWS condition keys.
- `DoctorRegisterLambdaExecutionRole` — `CKV_AWS_111`: VPC Lambda ENI creation requires wildcard EC2 permissions; permissions are limited to network-interface actions.
- `PatientRegisterLambdaExecutionRole` — `CKV_AWS_111`: VPC Lambda ENI creation requires wildcard EC2 permissions; permissions are limited to network-interface actions.
- `LoginLambdaExecutionRole` — `CKV_AWS_111`: VPC Lambda ENI creation requires wildcard EC2 permissions; permissions are limited to network-interface actions.

### Data_Stack.yaml

- `MySQLDatabase` — `CKV_AWS_118`: RDS enhanced monitoring requires an additional IAM role and operational approval; deferred for dev environment.
- `MySQLDatabase` — `CKV_AWS_157`: Dev database is intentionally single-AZ for cost; enable Multi-AZ in production.
- `MySQLDatabase` — `CKV_AWS_161`: Application uses Secrets Manager credentials; IAM database authentication is not part of this milestone.
- `SlotInventoryTable` — `CKV_AWS_119`: Project encryption policy uses AWS-managed encryption rather than customer-managed KMS keys; encryption at rest remains enabled.
- `IdempotencyTable` — `CKV_AWS_119`: Project encryption policy uses AWS-managed encryption rather than customer-managed KMS keys; encryption at rest remains enabled.
- `RefreshTokensTable` — `CKV_AWS_119`: Project encryption policy uses AWS-managed encryption rather than customer-managed KMS keys; encryption at rest remains enabled.
- `RevokedTokensTable` — `CKV_AWS_119`: Project encryption policy uses AWS-managed encryption rather than customer-managed KMS keys; encryption at rest remains enabled.
- `WaitlistTable` — `CKV_AWS_119`: Project encryption policy uses AWS-managed encryption rather than customer-managed KMS keys; encryption at rest remains enabled.
- `NotificationDispatchTable` — `CKV_AWS_119`: Project encryption policy uses AWS-managed encryption rather than customer-managed KMS keys; encryption at rest remains enabled.
- `ServerAccessLogsBucket` — `CKV_AWS_18`: Destination for S3 server access logs; logging this bucket to itself would create a recursive logging configuration.

### Application_Stack.yaml

- `DBInitLambda` — `CKV_AWS_173`: Lambda environment values are encrypted at rest with the AWS-managed Lambda key; customer-managed key is outside project key policy.
- `DBInitLambda` — `CKV_AWS_116`: This Lambda is invoked synchronously; Lambda async DLQ does not apply to synchronous API Gateway invocations.
- `DoctorRegisterLambda` — `CKV_AWS_173`: Lambda environment values are encrypted at rest with the AWS-managed Lambda key; customer-managed key is outside project key policy.
- `DoctorRegisterLambda` — `CKV_AWS_116`: This Lambda is invoked synchronously; Lambda async DLQ does not apply to synchronous API Gateway invocations.
- `PatientRegisterLambda` — `CKV_AWS_173`: Lambda environment values are encrypted at rest with the AWS-managed Lambda key; customer-managed key is outside project key policy.
- `PatientRegisterLambda` — `CKV_AWS_116`: This Lambda is invoked synchronously; Lambda async DLQ does not apply to synchronous API Gateway invocations.
- `LoginLambda` — `CKV_AWS_173`: Lambda environment values are encrypted at rest with the AWS-managed Lambda key; customer-managed key is outside project key policy.
- `LoginLambda` — `CKV_AWS_116`: This Lambda is invoked synchronously; Lambda async DLQ does not apply to synchronous API Gateway invocations.
- `TokenLambda` — `CKV_AWS_173`: Lambda environment values are encrypted at rest with the AWS-managed Lambda key; customer-managed key is outside project key policy.
- `TokenLambda` — `CKV_AWS_116`: This Lambda is invoked synchronously; Lambda async DLQ does not apply to synchronous API Gateway invocations.
- `TokenLambda` — `CKV_AWS_117`: Function does not require RDS network access; keeping it outside the VPC preserves access to regional AWS APIs without extra endpoints.
- `AuthorizerLambda` — `CKV_AWS_173`: Lambda environment values are encrypted at rest with the AWS-managed Lambda key; customer-managed key is outside project key policy.
- `AuthorizerLambda` — `CKV_AWS_116`: This Lambda is invoked synchronously; Lambda async DLQ does not apply to synchronous API Gateway invocations.
- `AuthorizerLambda` — `CKV_AWS_117`: Function does not require RDS network access; keeping it outside the VPC preserves access to regional AWS APIs without extra endpoints.
- `AuthTestLambda` — `CKV_AWS_173`: Lambda environment values are encrypted at rest with the AWS-managed Lambda key; customer-managed key is outside project key policy.
- `AuthTestLambda` — `CKV_AWS_116`: This Lambda is invoked synchronously; Lambda async DLQ does not apply to synchronous API Gateway invocations.
- `AuthTestLambda` — `CKV_AWS_117`: Function does not require RDS network access; keeping it outside the VPC preserves access to regional AWS APIs without extra endpoints.
- `DoctorRegisterLogGroup` — `CKV_AWS_158`: CloudWatch Logs provides encryption at rest by default; project policy does not provision customer-managed KMS keys.
- `PatientRegisterLogGroup` — `CKV_AWS_158`: CloudWatch Logs provides encryption at rest by default; project policy does not provision customer-managed KMS keys.
- `LoginLogGroup` — `CKV_AWS_158`: CloudWatch Logs provides encryption at rest by default; project policy does not provision customer-managed KMS keys.
- `TokenLogGroup` — `CKV_AWS_158`: CloudWatch Logs provides encryption at rest by default; project policy does not provision customer-managed KMS keys.
- `AuthorizerLogGroup` — `CKV_AWS_158`: CloudWatch Logs provides encryption at rest by default; project policy does not provision customer-managed KMS keys.
- `AuthTestLogGroup` — `CKV_AWS_158`: CloudWatch Logs provides encryption at rest by default; project policy does not provision customer-managed KMS keys.
- `AuthApiAccessLogGroup` — `CKV_AWS_158`: CloudWatch Logs provides encryption at rest by default; project policy does not provision customer-managed KMS keys.
- `DoctorRegisterMethod` — `CKV_AWS_59`: Public authentication bootstrap endpoint; request validation and application-level protections remain necessary; JWT is not available before login.
- `PatientRegisterMethod` — `CKV_AWS_59`: Public authentication bootstrap endpoint; request validation and application-level protections remain necessary; JWT is not available before login.
- `DoctorLoginMethod` — `CKV_AWS_59`: Public authentication bootstrap endpoint; request validation and application-level protections remain necessary; JWT is not available before login.
- `PatientLoginMethod` — `CKV_AWS_59`: Public authentication bootstrap endpoint; request validation and application-level protections remain necessary; JWT is not available before login.
- `RefreshMethod` — `CKV_AWS_59`: Public authentication bootstrap endpoint; request validation and application-level protections remain necessary; JWT is not available before login.
- `AuthStage` — `CKV_AWS_120`: Authentication and token responses must not be cached; throttling and logging are enabled.
