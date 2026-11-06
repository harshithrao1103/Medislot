# Milestone 4 implementation notes

Patient registration/login use existing `/auth/patients/register` and `/auth/patients/login`. Patient profile is `/patients/me` GET/PUT/DELETE, derived exclusively from authorizer context; profile creation occurs on registration. Public read-only discovery endpoints: `/doctors` (name, hospital, specialization, city, page, page_size), `/doctors/{doctorId}`, `/doctors/{doctorId}/slots?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`. The dashboard uses `/patient/*` routes in the same Flask app and EC2.

Database init adds indexes without resetting data. Index limitations documented in `database/MILESTONE4_INDEXES.md`. Public search uses substring LIKE, which is functional but B-tree indexes do not fully optimize; benchmark with EXPLAIN.

Security: patient identity from authorizer, no cross-patient ID route; public endpoints return professional data only; availability queries use existing DoctorStartIndex and exclude full, held, inactive and past slots. Deleting a patient with appointment history is rejected (409). This does not revoke existing refresh tokens on successful deletion; a follow-up token-revocation workflow should be added before production.

Validation: run `pytest -q tests/test_milestone4.py`, all existing tests, `cfn-lint Cloudformation/*.yaml`, then deploy dev via GitHub Actions. CI includes `tests/smoke_patient_domain.py` after auth smoke; live AWS checks are not executed locally. HTTPS is required before using real patient data.
