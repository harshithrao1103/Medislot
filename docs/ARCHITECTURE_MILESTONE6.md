# MediSlot architecture — Milestone 6

```mermaid
flowchart TB
  Patient[Patient browser] --> Flask[Patient Flask dashboard on EC2]
  Flask --> APIGW[API Gateway + JWT authorizer]
  APIGW --> M6[Milestone 6 Lambda]
  APIGW --> Appointment[Appointment API Lambda]
  M6 --> Waitlist[(DynamoDB Waitlist)]
  M6 --> Slots[(DynamoDB Slots)]
  M6 --> RDS[(RDS MySQL appointments)]
  M6 --> SFN[Step Functions reschedule Saga]
  SFN --> M6
  Appointment --> Slots
  Appointment --> RDS
  RDS --> Outbox[Existing RDS event_outbox]
  Outbox --> EB[EventBridge appointment bus]
  EB --> Promoter[Waitlist promoter Lambda]
  EB --> Notifier[Notification dispatcher Lambda]
  Scheduler[EventBridge Scheduler] --> Expiry[Offer expiry worker]
  Scheduler --> Reminders[Reminder dispatcher]
  Expiry --> Waitlist
  Expiry --> Slots
  Promoter --> Waitlist
  Promoter --> Slots
  Notifier --> Dispatch[(DynamoDB NotificationDispatch)]
  Notifier --> SES[Amazon SES]
```

The Milestone 6 Lambda currently hosts API handlers and worker operations in one deployable package. RDS is still the durable appointment record; DynamoDB provides conditional concurrency controls. Private interface endpoints provide SSM, EventBridge, Step Functions and SES API connectivity without a NAT Gateway.
