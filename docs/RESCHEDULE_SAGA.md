# Reschedule Saga and compensation

The API validates patient ownership, CONFIRMED status, configured attempt limit (2), configured cutoff (6 hours), and target slot date before starting Step Functions asynchronously.

```mermaid
flowchart TD
  A[Start execution] --> B[Claim target seat]
  B -->|success| C[Release original seat]
  B -->|failure or uncertain outcome| X[Compensate]
  C -->|success| D[Update RDS appointment + outbox in one transaction]
  C -->|failure| X
  D -->|success| E[Succeeded]
  D -->|failure| X
  X --> F[Restore original if released]
  F --> G[Release target if claimed]
  G --> H[Failed]
```

DynamoDB step markers make target claim, original release and compensation idempotent. A deterministic RDS outbox event ID makes the RDS commit retry-safe. The same appointment row is updated; failed attempts do not increment `reschedule_count`.
