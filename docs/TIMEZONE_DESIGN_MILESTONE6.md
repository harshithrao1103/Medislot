# Timezone design

Slot inventory timestamps are stored in UTC and existing slot records retain the source timezone metadata. The patient dashboard's date selection is interpreted in `Asia/Kolkata` and calls the existing doctor slot availability API using `YYYY-MM-DD` date boundaries. Reminder comparisons are performed against UTC `start_time_utc` values; no browser-local timezone is used for reminder dispatch. Existing materialisation behavior, including DST handling for doctor-local weekly availability, is unchanged.

Example: a patient in India selecting 2026-11-28 requests the local date range 2026-11-28 through 2026-11-28. The existing slot API converts the local midnight boundaries to UTC before querying the DynamoDB slot index. Reminder dispatch compares UTC instants, not the displayed IST string.
