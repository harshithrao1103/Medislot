# MediSlot Milestone 6 — local test report

Date: 2026-11-27

## Executed

Command:

```bash
python -m pip install -r tests/requirements.txt
AWS_DEFAULT_REGION=us-east-1 AWS_EC2_METADATA_DISABLED=true \
python -m pytest -q tests \
  --ignore=tests/smoke_auth.py --ignore=tests/smoke_patient_domain.py --ignore=tests/smoke_milestone6.py
```

Result: **58 passed, 4 skipped, 4 warnings**. The warnings are the pre-existing JWT HMAC test fixtures using keys shorter than the recommended 32 bytes.

Python compilation also passed for all 38 project Python source files. The four Milestone 6 rule tests cover FIFO ordering, offer-expiry boundary behavior, deterministic recipient-specific notification dispatch IDs, and reschedule attempt/cutoff rules. Six local integration-contract tests validate CloudFormation resources, API routes, the Saga compensation state, no-NAT/no-SNS-SQS constraints, RDS lock schema, and Flask dashboard wiring. The four AWS integration tests are present but skipped locally because `M6_RUN_AWS_INTEGRATION` and deployed fixtures are not configured.

## Not executed / blocked locally

The existing authentication and patient-domain smoke-test modules, plus `smoke_milestone6.py`, require a live AWS deployment and external credentials and were not run locally. Flask is not installed in this local environment, so the Flask app was syntax-compiled and the three changed Jinja templates were parsed, but the server was not booted for browser-level testing.

The four mandatory deployed-AWS integration scenarios are implemented in `tests/integration/test_milestone6_aws.py` and are configured as a required post-deploy step in `deploy.yaml`:

1. 20+ simultaneous requests against a capacity-3 slot with exactly three successes.
2. Repeated booking request with one Idempotency-Key creating one appointment.
3. Forced reschedule release failure and compensation preserving exactly one appointment.
4. One freed seat with three waitlisted patients and never more than one live offer.

They were **not executed in this work session** because no AWS deployment credentials or isolated test fixtures were available. The deployment workflow requires the GitHub Variables and Secrets listed in `docs/AWS_INTEGRATION_TEST_FIXTURES.md`; deployment will fail if those fixtures are missing or a mandatory scenario fails. CloudFormation templates and embedded Step Functions/dashboard JSON were parsed locally, but `cfn-lint`, Checkov and AWS `validate-template`/change-set execution were not run in this work session. No SES sender identity was verified. No restore drill was performed.
