# Concurrency and idempotency design

## Booking and held seats

The existing booking reservation uses DynamoDB optimistic concurrency (`version`, `bookedCount`, `capacity`) and a conditional update. Milestone 6 adds `attribute_not_exists(holdOfferId)` to that same conditional write. A live or expired-but-not-yet-swept hold therefore cannot be bypassed by normal booking. The expiry worker clears the hold before a later booking can proceed.

## Waitlist registration

The active count update, entry put and patient/slot duplicate-guard put occur in one DynamoDB transaction. Concurrent registrations serialize through the conditional `activeWaitlistCount < 5` update and conditional duplicate guard. Leave, acceptance and expiry update the entry, decrement the counter and delete the guard in one transaction. Every decrement checks `activeWaitlistCount >= 1`.

## Exclusive offers

The slot hold and waitlist entry transition from WAITING to OFFERED occur in one DynamoDB transaction. The slot condition requires AVAILABLE status, free capacity and no existing `holdOfferId`. A second promoter cannot create another live offer for the same slot seat. Normal booking and reschedule target claims reject any present hold attribute until the expiry transaction clears it.

## Reschedule Saga

Each DynamoDB seat operation has a deterministic marker under `PK=SAGA#sagaId`. A transaction writes the marker and changes the seat together. Replayed Step Functions states observe the marker and do not repeat the seat delta. The RDS outbox event ID is deterministic per saga; the appointment update, count increment, lock completion and outbox insertion are one RDS transaction. Compensation checks the step markers and applies each inverse delta at most once.

`reschedule_locks` prevents concurrent cancellation, doctor cancellation, and outcome updates while the Saga is pending. If compensation itself cannot complete, the lock remains PENDING for operator review rather than allowing a cancellation to race with an uncertain seat state.

## Notification dispatch

A deterministic recipient-specific SHA-256 dispatch ID is conditionally inserted before SES. A second worker cannot claim it. If SES returns an uncertain result, status becomes UNKNOWN and is never retried automatically, prioritizing duplicate prevention over guaranteed delivery.
