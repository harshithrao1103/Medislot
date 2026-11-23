# Lambda environment KMS deployment permission

The Appointment API Lambda uses the existing MediSlot customer-managed KMS key through
`KmsKeyArn` in `Cloudformation/Application_Stack.yaml`.

The CloudFormation execution role (`medislot_cloudformation_execution`) is created
outside these CloudFormation stacks. Its current inline policies do not grant the
required KMS operations on that existing key. Apply the narrowly scoped inline policy
once before running the deployment workflow:

```powershell
aws iam put-role-policy `
  --role-name medislot_cloudformation_execution `
  --policy-name MediSlotLambdaEnvironmentKmsPolicy `
  --policy-document file://Cloudformation/CloudFormationExecutionRoleKmsPolicy.json
```

This grants only `kms:CreateGrant`, `kms:DescribeKey`, `kms:Encrypt`, and
`kms:GenerateDataKey` on the existing key. It does not create or replace a KMS key,
does not change the key policy, and does not modify any live resources until this
command is explicitly run.

The Appointment API Lambda execution role already has `kms:Decrypt` scoped to the
same key in `Cloudformation/Security_IAM_Stack.yaml`.

After applying the policy, rerun GitHub Actions. Do not deploy if the KMS key policy
or key state has changed since it was last checked.
