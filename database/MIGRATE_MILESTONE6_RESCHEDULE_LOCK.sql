-- Run once against the existing MediSlotDB before deploying Milestone 6.
-- This lock coordinates the asynchronous Saga with concurrent patient/doctor cancellation.
CREATE TABLE IF NOT EXISTS reschedule_locks (
  appointment_id BIGINT PRIMARY KEY,
  saga_id VARCHAR(64) NOT NULL,
  patient_id BIGINT NOT NULL,
  execution_arn VARCHAR(256) NULL,
  status ENUM('PENDING','COMPLETED','FAILED') NOT NULL DEFAULT 'PENDING',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_reschedule_lock_patient (patient_id,status)
) ENGINE=InnoDB;


-- The reminder dispatcher repeatedly selects upcoming CONFIRMED appointments.
-- Add the supporting index only when it is not already present.
SET @m6_reminder_index_exists = (
  SELECT COUNT(*) FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'appointments'
    AND index_name = 'idx_appointment_status_start_time'
);
SET @m6_reminder_index_sql = IF(
  @m6_reminder_index_exists = 0,
  'CREATE INDEX idx_appointment_status_start_time ON appointments(status,start_time_utc)',
  'SELECT 1'
);
PREPARE m6_reminder_index_stmt FROM @m6_reminder_index_sql;
EXECUTE m6_reminder_index_stmt;
DEALLOCATE PREPARE m6_reminder_index_stmt;


SET @m6_execution_arn_exists = (
  SELECT COUNT(*) FROM information_schema.columns
  WHERE table_schema = DATABASE() AND table_name = 'reschedule_locks' AND column_name = 'execution_arn'
);
SET @m6_execution_arn_sql = IF(
  @m6_execution_arn_exists = 0,
  'ALTER TABLE reschedule_locks ADD COLUMN execution_arn VARCHAR(256) NULL AFTER patient_id',
  'SELECT 1'
);
PREPARE m6_execution_arn_stmt FROM @m6_execution_arn_sql;
EXECUTE m6_execution_arn_stmt;
DEALLOCATE PREPARE m6_execution_arn_stmt;
