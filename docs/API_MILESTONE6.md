# Milestone 6 API documentation

All endpoints below require a valid patient JWT. Patient identity is read from the API Gateway authorizer context; request-body identity fields are not used.

| Method | Path | Request | Success |
|---|---|---|---|
| POST | `/slots/{slotId}/waitlist` | Path `slotId` | 201 with `entryId` and `WAITING`; 409 when slot is not full, patient already joined, or five active waitlists reached |
| DELETE | `/slots/{slotId}/waitlist` | Path `slotId` | 200 with `LEFT`; an offered seat is released and promotion is attempted |
| GET | `/patients/me/waitlists` | None | 200 with caller's waitlist entries |
| POST | `/waitlist-offers/{offerId}/accept` | Path `offerId` | 201 with confirmed appointment ID; 409 if offer is expired or no longer owned/held |
| PATCH | `/appointments/{appointmentId}` | JSON `{ "slotId": "target-slot-id" }` | 202 with Step Functions `executionArn` and `PENDING` |
| GET | `/reschedule-executions?executionArn=...` | Query execution ARN | 200 with `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `TIMED_OUT` or `ABORTED` |

Errors use JSON `{ "error": "..." }` and appropriate 400/401/403/404/409/500 status codes. The status endpoint checks the execution input's patient ID before returning its status.
