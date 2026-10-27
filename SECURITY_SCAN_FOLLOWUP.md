# MediSlot security follow-up (2026-10-27)

Changes in this revision:
- The `/auth/logout` method uses the existing JWT Lambda authorizer. Other registration, login, and refresh methods stay public as required by authentication flow.
- DB initialization Lambda has a reserved concurrency limit of 1 to avoid multiple simultaneous schema initializations.
- S3 profile photo and SAM artifact buckets now deliver server access logs to a separate bucket with a 90-day expiration rule and log-delivery service principal bucket policy.

## Important deployment considerations
- This adds a new S3 bucket and bucket policy; CloudFormation execution role must be able to create/manage them.
- Confirm that API Gateway callers send `Authorization: Bearer <access-token>` on logout; the authorizer must support that format.
- Review the CloudFormation change set. Do not approve any replacement of the existing RDS instance.
- CloudFormation cannot guarantee bucket policy propagation before logging is enabled. If the first update fails with an S3 logging delivery error, separate creation of the log destination/policy and enabling logging into two deployments.
- Checkov exceptions have NOT been broadly added. Remaining findings (including RDS, KMS, IAM, and synchronous Lambda DLQ rules) need explicit security/architecture decisions before a green scan can be claimed.
- This ZIP has not been deployed or verified by the GitHub CI workflow.
