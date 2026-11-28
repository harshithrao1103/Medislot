# RDS ERD and schema notes — Milestone 6

```mermaid
erDiagram
  USERS ||--o| PATIENTS : profile
  USERS ||--o| DOCTORS : profile
  PATIENTS ||--o{ APPOINTMENTS : books
  DOCTORS ||--o{ APPOINTMENTS : receives
  APPOINTMENTS ||--o{ REFUND_LEDGER : refund_history
  APPOINTMENTS ||--o{ RESCHEDULE_LOCKS : saga_coordination
  APPOINTMENTS ||--o{ EVENT_OUTBOX : emits

  USERS {
    bigint user_id PK
    varchar email
    enum role
  }
  APPOINTMENTS {
    bigint appointment_id PK
    bigint patient_id FK
    bigint doctor_id FK
    varchar slot_id
    datetime start_time_utc
    enum status
    decimal fee
    tinyint reschedule_count
    varchar idempotency_key
  }
  REFUND_LEDGER {
    bigint refund_id PK
    bigint appointment_id
    decimal refund_amount
    decimal deduction_amount
  }
  RESCHEDULE_LOCKS {
    bigint appointment_id PK
    varchar saga_id
    bigint patient_id
    varchar execution_arn
    enum status
  }
  EVENT_OUTBOX {
    varchar event_id PK
    varchar aggregate_type
    varchar aggregate_id
    varchar event_type
    json payload
    enum status
  }
```

`appointments.reschedule_count` is reused and remains capped at 2. The new `reschedule_locks` coordination table has no foreign-key dependency; it serializes the asynchronous Saga against cancellation/outcome updates. Appointment changes and the `AppointmentRescheduled` outbox event are committed in one RDS transaction.
