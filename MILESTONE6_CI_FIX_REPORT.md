# MediSlot Milestone 6 CI workflow adjustment

## Changes made

- Removed the `Generate fresh Milestone 6 patient accounts and JWTs` step from `.github/workflows/deploy.yaml`.
- Removed the `Run four mandatory deployed-AWS Milestone 6 integration tests` step and its `M6_TEST_*` repository-variable requirements from the deployment workflow.
- Removed `tests/integration/setup_milestone6_patients.py`, which existed solely to create disposable patient accounts for the removed workflow step.
- Kept `tests/integration/test_milestone6_aws.py` in the repository for deliberate, manual deployed-AWS testing.
- Updated `docs/AWS_INTEGRATION_TEST_FIXTURES.md` to explain that its fixture values and fresh patient tokens are for manual execution only, not GitHub Actions repository Variables.
- Kept the separate live authentication/patient-domain/Milestone 6 smoke tests, deployment jobs, canonical Slots table verification, infrastructure verification, and no-NAT checks unchanged.

## Expected workflow behavior

The deployment workflow will no longer register 20 disposable patients and will no longer fail because `M6_TEST_DOCTOR_ID`, slot fixture IDs, or `M6_TEST_PATIENT_TOKENS_JSON` are missing. Other deployment and validation steps remain in place.

## Validation

- Workflow YAML parsing: checked after the edit.
- Confirmed the two named Milestone 6 integration steps are absent.
- Confirmed the separate smoke-test step and downstream infrastructure verification steps remain present.
- No AWS deployment or live integration test was run as part of this ZIP edit.

## Manual testing note

The deployed-AWS Milestone 6 integration test source remains available at `tests/integration/test_milestone6_aws.py`. It performs live mutations and must only be run deliberately against isolated test fixtures. See `docs/AWS_INTEGRATION_TEST_FIXTURES.md`.
