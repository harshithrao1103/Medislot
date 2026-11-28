# Waitlist FIFO and exclusive hold design

1. A patient can register only while the slot is full and AVAILABLE.
2. A registration uses `ENTRY#joinedAt#entryId`; query sort order is FIFO.
3. The promoter skips non-WAITING records and claims the first eligible entry.
4. One DynamoDB transaction conditionally sets `holdOfferId`, `holdPatientId`, `holdExpiresAt` on the Slots item and changes the matching waitlist record to OFFERED.
5. The hold lasts 900 seconds by default and is read from SSM.
6. Normal booking and reschedule target claims reject any slot item containing `holdOfferId`.
7. Acceptance verifies patient ownership, offer state, expiry, and exact hold ownership before incrementing bookedCount.
8. Leave and expiry clear the hold and update the waitlist/counter/duplicate guard atomically; promotion is then attempted for the next FIFO patient.
9. TTL is only eventual record cleanup. The one-minute scheduler performs the expiry business transition.

The guarantee is one live offer per slot seat at a time, enforced by the slot's conditional write/transaction—not by a preceding application-level read.
