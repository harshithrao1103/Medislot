# MediSlot GitHub Actions Checkov fixes

This update addresses the six CloudFormation security findings previously reported by GitHub Actions.

## Findings and changes

| Checkov ID | Resource | Change |
|---|---|---|
| CKV_AWS_158 | `Milestone6LogGroup` | Reused the existing `CloudWatchLogsKmsKeyArn` export. |
| CKV_AWS_158 | `RescheduleLogGroup` | Reused the existing `CloudWatchLogsKmsKeyArn` export. |
| CKV_AWS_173 | `Milestone6Lambda` | Configured `KmsKeyArn` to use the existing customer-managed key. The shared `AppointmentApiLambdaRole` already has `kms:Decrypt` permission for this key. |
| CKV_AWS_115 | `Milestone6Lambda` | Added a Checkov exception consistent with the project's existing development shared-concurrency policy. No arbitrary reserved concurrency limit was introduced. |
| CKV_AWS_116 | `Milestone6Lambda` | Added a documented Checkov exception. SQS/SNS DLQs are excluded by project architecture; API Gateway paths are synchronous, Step Functions tasks have explicit retries/catches, and EventBridge/Scheduler use managed retry behavior. |
| CKV_AWS_119 | `WaitlistTable` | Added a documented exception consistent with the other DynamoDB tables. DynamoDB server-side encryption remains enabled; this project does not provision customer-managed KMS keys for DynamoDB tables. |

## KMS key policy update

The existing `CloudWatchLogsKmsKey` policy was extended to permit the CloudWatch Logs service to use the key with the encryption contexts for:
- `/aws/lambda/medislot-${Environment}-milestone6`
- `/aws/vendedlogs/states/medislot-${Environment}-reschedule`

The existing appointment-api and patient-domain log group contexts remain unchanged.

## Preserved architecture

- No NAT Gateway added.
- No SQS or SNS resources added.
- No DynamoDB table key schema or data migration performed.
- No AWS deployment or live resource changes performed.

## Local validation performed

- All four CloudFormation YAML files parsed successfully with PyYAML and CloudFormation custom tags handled.
- Python `compileall` completed successfully for `lambdas`, `tests`, and `doctor_dashboard`.
- `pytest` collection could not complete in this environment because the dependencies `bcrypt` and `pymysql` are not installed.
- `cfn-lint` and Checkov could not be executed here because those tools are not installed and package installation was unavailable.

**Important:** Run the repository's GitHub Actions workflow after extracting this ZIP to confirm the exact Checkov and cfn-lint results in the project's CI environment. The Checkov exceptions are documented policy decisions, not proof that a DLQ or customer-managed DynamoDB key exists.
