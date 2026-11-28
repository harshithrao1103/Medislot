# MediSlot — AWS doctor appointment platform

This repository contains the existing MediSlot application: Python AWS Lambda APIs, API Gateway, RDS MySQL, DynamoDB slot inventory, EventBridge outbox events, CloudFormation stacks, Flask patient/doctor dashboards, and GitHub Actions workflows.

## Milestone 6 additions in this source tree

- Waitlist table redesign using `PK` / `SK`, FIFO entries, `PatientWaitlistIndex`, patient metadata counters and duplicate guards.
- Exclusive slot holds with a configurable 15-minute expiry, EventBridge promotion on `SlotSeatFreed`, and a one-minute expiry sweep.
- Asynchronous Step Functions reschedule Saga with idempotent DynamoDB step markers, RDS `reschedule_locks`, compensation and status polling.
- EventBridge → Lambda → SES notifications with DynamoDB at-most-once dispatch claims, plus 24-hour and 2-hour reminders.
- Patient Flask pages for waitlist management and asynchronous rescheduling.
- CloudFormation updates for tables, SSM parameters, private service endpoints, API methods, schedules, state machine, dashboard and alarms.

## Deployment caution

This is source code and infrastructure-as-code. Before deployment, run `database/MIGRATE_MILESTONE6_RESCHEDULE_LOCK.sql` against the existing database and configure the verified SES sender at `/medislot/${Environment}/milestone6/ses-sender-email`. Deploy CloudFormation in the documented order and inspect every change set, especially the Waitlist table key-schema replacement. **No AWS deployment or live SES verification is claimed by this repository package.**

See `docs/MILESTONE6_IMPLEMENTATION.md`, `docs/ARCHITECTURE_MILESTONE6.md`, `docs/AWS_EVENT_FLOW_M6.md`, `docs/RDS_ERD_MILESTONE6.md`, `docs/DYNAMODB_DATA_MODEL_MILESTONE6.md`, `docs/CONCURRENCY_DESIGN_MILESTONE6.md`, `docs/RESCHEDULE_SAGA.md`, `docs/WAITLIST_EXCLUSIVITY.md`, `docs/TIMEZONE_DESIGN_MILESTONE6.md`, `docs/API_MILESTONE6.md`, `docs/SES_SETUP.md`, `docs/DISASTER_RECOVERY_MILESTONE6.md`, `docs/TEST_REPORT_MILESTONE6.md`, and `docs/AWS_INTEGRATION_TEST_FIXTURES.md`.

## Local tests

The recorded local regression command and results are in `docs/TEST_REPORT_MILESTONE6.md`. Deployed-AWS integration tests and the disaster-recovery restore drill remain separate deployment-time verification tasks.
