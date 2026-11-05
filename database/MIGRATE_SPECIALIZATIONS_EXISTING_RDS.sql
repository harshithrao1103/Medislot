-- RUN ONLY ON AN EXISTING DATABASE, after taking a verified RDS snapshot.
-- MySQL DDL auto-commits: run in maintenance window before deploying new Lambda code.
-- Preflight first: inspect SELECT DISTINCT specialization FROM doctors;
-- Resolve unknown names by adding master entries or updating legacy values.
CREATE TABLE IF NOT EXISTS specializations (
 specialization_id BIGINT AUTO_INCREMENT PRIMARY KEY,
 specialization_name VARCHAR(100) NOT NULL UNIQUE
) ENGINE=InnoDB;
INSERT IGNORE INTO specializations(specialization_name) VALUES
('Cardiology'),
('Dermatology'),
('Neurology'),
('Orthopedics'),
('Pediatrics'),
('General Medicine'),
('Gynecology'),
('Psychiatry'),
('ENT'),
('Ophthalmology'),
('Gastroenterology'),
('Pulmonology'),
('Urology'),
('Oncology');

-- Preserve unique existing names rather than discard data. Correct common typo explicitly.
UPDATE doctors SET specialization='Dermatology'
WHERE LOWER(TRIM(specialization)) IN ('dermotologist','dermatologist','dermatology');
UPDATE doctors SET specialization='Cardiology'
WHERE LOWER(TRIM(specialization)) IN ('cardiologist','cardiology');
UPDATE doctors SET specialization='Neurology'
WHERE LOWER(TRIM(specialization)) IN ('neurologist','neurology');
INSERT IGNORE INTO specializations(specialization_name)
 SELECT DISTINCT TRIM(specialization) FROM doctors WHERE TRIM(specialization) <> '';

ALTER TABLE doctors ADD COLUMN specialization_id BIGINT NULL;
UPDATE doctors d JOIN specializations s
 ON LOWER(TRIM(d.specialization)) = LOWER(s.specialization_name)
 SET d.specialization_id = s.specialization_id;

-- Stop before destructive DDL if any doctor was not mapped.
DELIMITER $$
CREATE PROCEDURE assert_doctor_specializations_migrated()
BEGIN
 IF EXISTS (SELECT 1 FROM doctors WHERE specialization_id IS NULL) THEN
   SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Unmapped doctor specialization; resolve before migration';
 END IF;
END$$
DELIMITER ;
CALL assert_doctor_specializations_migrated();
DROP PROCEDURE assert_doctor_specializations_migrated;

ALTER TABLE doctors MODIFY specialization_id BIGINT NOT NULL;
ALTER TABLE doctors ADD CONSTRAINT fk_doctors_specialization
 FOREIGN KEY (specialization_id) REFERENCES specializations(specialization_id);
-- Retire old free-text field only after successful mapping.
ALTER TABLE doctors DROP COLUMN specialization;
-- Verify: SELECT d.user_id,s.specialization_name FROM doctors d
-- JOIN specializations s ON s.specialization_id=d.specialization_id;
