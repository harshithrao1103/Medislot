# AWS service and event flow — Milestone 6

```mermaid
sequenceDiagram
  participant API as API Gateway
  participant M6 as Milestone6 Lambda
  participant DDB as DynamoDB
  participant RDS as RDS MySQL
  participant SFN as Step Functions
  participant EB as EventBridge
  participant SES as SES
  API->>M6: Patient waitlist/reschedule API
  M6->>DDB: Conditional transaction / saga markers
  M6->>RDS: Appointment + reschedule lock
  M6->>SFN: StartExecution (async)
  SFN->>M6: Claim / release / commit / compensate
  RDS-->>EB: Existing outbox dispatcher
  EB->>M6: SlotSeatFreed / notification event
  M6->>DDB: Claim NotificationDispatch ID
  M6->>SES: SendEmail
  EB->>M6: Waitlist offer events
  Note over M6,DDB: Scheduler runs expiry every minute and reminders every five minutes
```

Private Lambda traffic reaches SSM, EventBridge, Step Functions and SES through interface VPC endpoints. DynamoDB uses the existing Gateway endpoint. No NAT Gateway is part of the design.
