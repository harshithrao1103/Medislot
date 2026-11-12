-- MediSlot migration: split the legacy users.name value into first_name and last_name.
-- Take a verified RDS snapshot before running this migration.
-- Run once against the existing MediSlot database before deploying the updated Lambdas.
-- The legacy users.name column is retained and kept in sync for backward compatibility.

ALTER TABLE users
  ADD COLUMN first_name VARCHAR(50) NULL AFTER name,
  ADD COLUMN last_name VARCHAR(50) NULL AFTER first_name;

UPDATE users
SET
  first_name = LEFT(SUBSTRING_INDEX(TRIM(name), ' ', 1), 50),
  last_name = CASE
    WHEN LOCATE(' ', TRIM(name)) > 0
      THEN LEFT(TRIM(SUBSTRING(TRIM(name), LOCATE(' ', TRIM(name)) + 1)), 50)
    ELSE ''
  END
WHERE first_name IS NULL OR last_name IS NULL;

ALTER TABLE users
  MODIFY COLUMN first_name VARCHAR(50) NOT NULL,
  MODIFY COLUMN last_name VARCHAR(50) NOT NULL;

-- Verify the migration before deploying:
-- SELECT user_id, name, first_name, last_name FROM users ORDER BY user_id;
