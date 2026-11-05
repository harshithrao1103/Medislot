# Specialization dropdown implementation

The doctor registration and profile pages now retrieve specialization choices using public `GET /specializations`. Registration sends an integer `specialization_id`, and authenticated `PUT /doctors/me` validates the ID then updates the existing doctor row. `GET /doctors/me` includes both `specialization_id` and human-readable `specialization`.

## Existing RDS: mandatory migration before deployment

1. Take and verify an RDS snapshot; schedule a maintenance window.
2. Review `SELECT DISTINCT specialization FROM doctors;` and the mappings in `database/MIGRATE_SPECIALIZATIONS_EXISTING_RDS.sql`.
3. Execute that SQL using an approved MySQL client / migration runner with database access. **Do not run `db_init` against the old schema first.** SQL contains MySQL `DELIMITER` commands, so use the MySQL CLI rather than a naive semicolon splitter. DDL auto-commits and cannot be rolled back as a single transaction.
4. Verify all doctors still exist and have a valid specialization ID; only then deploy the updated Lambda packages, API Gateway stack and dashboard via existing GitHub Actions.
5. Test registration, doctor profile GET, profile PUT, invalid IDs, and that existing doctor IDs and appointments remain unchanged.

For a fresh database, `database/schema.sql` creates the new table and seeds initial choices. The existing `consultation_types` table remains unchanged; consultation type normalization is a separate decision.

**No AWS resources or database rows were changed by producing this ZIP.** Existing deployment credentials and an actual MySQL connection are required to run the migration and verify the live application.
