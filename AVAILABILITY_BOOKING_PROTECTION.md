# Availability and doctor schedule changes

This update changes the doctor availability API and schedule response:

- Deleting a weekly availability pattern is rejected with HTTP 409 while it owns future `CONFIRMED` appointments.
- Editing a pattern is rejected with HTTP 409 if it would move/remove a future confirmed appointment or change the consultation type for a booked slot.
- Expanding a pattern is allowed when existing confirmed appointments remain fully inside the new pattern.
- Successful create, edit, and delete operations invoke the existing slot materialiser asynchronously so the 30-day slot window is reconciled immediately.
- The doctor schedule now includes generated `AVAILABLE` slots and any slot that still has a `CONFIRMED` appointment, even if the slot is `INACTIVE`. This keeps booked appointments visible after availability changes while preventing inactive slots from being presented as open capacity.

No database schema or DynamoDB key changes are required. The materialiser remains responsible for creating missing slots and disabling obsolete unbooked slots.

## Validation performed

- Python syntax compilation passed for the changed doctor API and slot materialiser modules.
- The full pytest suite could not be collected in this environment because runtime dependencies (`bcrypt` and `pymysql`) are not installed and package installation is unavailable.
