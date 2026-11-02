# MediSlot deployment review (2026-11-02)

## Changes made

- Removed UTF-8 BOM from `Cloudformation/Security_IAM_Stack.yaml`.
- Corrected Security IAM GitHub Actions parameters to only `Environment` and `VpcCIDR`.
- Added Milestone 3 IAM execution roles for Doctor API and Slot Materialiser, Scheduler role, EC2 instance role/profile, and dashboard security group.
- Added private Secrets Manager interface VPC endpoint because private subnets have no NAT route; reused the existing endpoint security group.
- Moved the not-yet-deployed Scheduler IAM role definition from Application Stack to Security IAM and imported its ARN.
- Aligned four existing RDS-secret KMS decrypt IAM statements with the KMS key ARN currently configured in Data Stack. This changes IAM policies, not the RDS key or database. Confirm the actual secret encryption key before deployment.
- Extended deployment trigger paths to dashboard and test changes.

## Local checks

- All four CloudFormation YAML files parse as YAML.
- All 25 CloudFormation cross-stack import names were matched to an output export name.
- `python -m pytest -q tests` could not collect tests in this container because `bcrypt` is not installed; GitHub CI installs `tests/requirements.txt` first.
- `cfn-lint` and `checkov` are unavailable in this container, so those checks remain for GitHub CI.
- No live AWS deployment or Change Set review has been performed.

## Important deployment prerequisites / cautions

1. **Do not use the EC2 doctor dashboard with real credentials:** it currently uses unencrypted HTTP on port 80 and the dashboard security group's default source CIDR is `0.0.0.0/0`. Restrict `DashboardIngressCidr` in the Security IAM deployment and configure HTTPS before real use.
2. The GitHub Actions workflow writes JWT SecureString using `aws ssm put-parameter`. This is an existing imperative step, not CloudFormation-managed. To meet strict CloudFormation-only provisioning, migrate it to a securely managed deployment design in a separate controlled change.
3. Confirm the real RDS master secret's KMS key and IAM key policy; template consistency is not proof that AWS grants decryption.
4. Confirm CloudFormation execution role permissions for EC2, Scheduler, IAM PassRole, Secrets Manager VPC endpoints, S3, Lambda, and API Gateway.
5. New Secrets Manager VPC endpoint and EC2 instance incur charges. Review the CloudFormation Change Set before applying.
6. Dashboard bootstrap needs outbound access to public Linux repositories, Python package indexes, API Gateway and S3. Inspect EC2 `/var/log/cloud-init-output.log` if bootstrap fails.
7. Existing Data Stack is preserved. **Do not execute** `database/RESET_EXISTING_SCHEMA_DEV_ONLY.sql`.
8. This ZIP excludes `.git` history and local caches to avoid disclosing repository metadata and unnecessary files.

## Deploy

Create a feature branch, commit the changed files, open a PR, and review CI and CloudFormation change sets. The workflow deploys on push to `main`; merging is a real deployment, not a dry run.
