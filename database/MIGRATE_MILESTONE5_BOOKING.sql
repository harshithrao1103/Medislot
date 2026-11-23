-- MediSlot Milestone 5 migration for an EXISTING RDS database.
-- Take and verify an RDS snapshot first. Run once; do not run against a fresh
-- database already initialized from the updated schema.sql.
-- Check existing duplicate active bookings/idempotency values before adding keys.

ALTER TABLE appointments
  ADD COLUMN idempotency_key VARCHAR(160) NULL,
  ADD COLUMN cancelled_at DATETIME NULL,
  ADD COLUMN cancellation_actor ENUM('PATIENT','DOCTOR','SYSTEM') NULL,
  ADD COLUMN active_booking_guard TINYINT
    GENERATED ALWAYS AS (CASE WHEN status='CONFIRMED' THEN 1 ELSE NULL END) STORED,
  ADD UNIQUE KEY uq_appointment_idempotency (idempotency_key),
  ADD UNIQUE KEY uq_active_patient_slot (patient_id,slot_id,active_booking_guard);

ALTER TABLE refund_ledger
  ADD UNIQUE KEY uq_refund_appointment (appointment_id);

CREATE TABLE event_outbox (
  event_id VARCHAR(64) PRIMARY KEY,
  aggregate_type VARCHAR(50) NOT NULL,
  aggregate_id VARCHAR(100) NOT NULL,
  event_type VARCHAR(100) NOT NULL,
  payload JSON NOT NULL,
  status ENUM('PENDING','PUBLISHED') NOT NULL DEFAULT 'PENDING',
  attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
  last_error VARCHAR(1000) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  published_at DATETIME NULL,
  INDEX idx_outbox_pending (status,created_at)
) ENGINE=InnoDB;
