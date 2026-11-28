# MediSlot Milestone 6 CI fixes

## Changes

- Added `tests/integration/setup_milestone6_patients.py`. Every deployment run registers 20 synthetic patients through the real patient registration API, logs them in through the real patient login API, and exports their fresh access JWTs to the current GitHub Actions job environment. JWTs are not stored in GitHub Secrets, printed, or committed.
- Updated `.github/workflows/deploy.yaml` to run that setup immediately before the four deployed-AWS Milestone 6 integration tests and removed the expired-token GitHub Secret dependencies.
- Updated `tests/integration/test_milestone6_aws.py` to allocate generated patients to concurrency, idempotency, reschedule-compensation, and waitlist tests. The reschedule test now creates its own appointment during the run instead of requiring a permanent patient JWT and appointment ID.
- Updated `docs/AWS_INTEGRATION_TEST_FIXTURES.md` to document the remaining slot/doctor repository Variables and the temporary account lifecycle.
- Updated `MediSlot-GitHubActions-SSM-Policy.json` with the SSM permissions required by the EC2 dashboard deployment step.

## Validation performed

- Python compilation of the new setup script and modified integration test: passed.
- GitHub Actions workflow YAML parse: passed.
- JSON policy parse: passed.
- `pytest -q tests/integration/test_milestone6_contract.py tests/test_milestone6_rules.py`: 10 passed.
- Full `pytest -q tests` could not complete in this environment because local packages `bcrypt` and `pymysql` are not installed. Install `tests/requirements.txt` and rerun in CI/local before deployment.
- Live AWS integration tests were not run; they require the deployed API, configured slot Variables, AWS credentials, and live resources.

## Required AWS-side action

The previous GitHub Actions log also showed `AccessDenied` for `ssm:SendCommand` under role `medislot_github_connection`. Updating a policy JSON file in the ZIP does not change the live IAM role. Apply the command documented in `docs/AWS_INTEGRATION_TEST_FIXTURES.md` using an AWS identity authorized to update that role. The `M6_TEST_*` slot/doctor repository Variables must also remain configured.

## Notes

- No NAT Gateway or NAT-related resource was added.
- The workflow creates uniquely named synthetic patient accounts on each run. The current project has no patient deletion endpoint, so these test-only accounts remain in the database; no real patient data is used.
