-- MediSlot finalized RDS MySQL schema (8 tables).
-- Safe for a fresh database; CREATE IF NOT EXISTS does NOT migrate old tables.
-- Existing deployments require a reviewed backup + migration before running db_init.
CREATE TABLE IF NOT EXISTS users (
  user_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  email VARCHAR(255) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('PATIENT','DOCTOR') NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS doctors (
  user_id BIGINT PRIMARY KEY,
  licence_number VARCHAR(100) NOT NULL UNIQUE,
  specialization VARCHAR(100) NOT NULL,
  experience_years SMALLINT UNSIGNED NOT NULL,
  qualifications TEXT NOT NULL,
  hospital_name VARCHAR(200) NOT NULL,
  clinic_address VARCHAR(500) NOT NULL,
  city VARCHAR(100) NOT NULL,
  languages JSON NOT NULL,
  timezone VARCHAR(64) NOT NULL,
  photo_key VARCHAR(512) NULL,
  CONSTRAINT fk_doctors_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS patients (
  user_id BIGINT PRIMARY KEY,
  date_of_birth DATE NOT NULL,
  gender VARCHAR(30) NOT NULL,
  phone VARCHAR(20) NOT NULL,
  city VARCHAR(100) NOT NULL,
  emergency_contact VARCHAR(100) NOT NULL,
  notes TEXT NULL,
  CONSTRAINT fk_patients_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS consultation_types (
  consultation_type_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  doctor_id BIGINT NOT NULL,
  name VARCHAR(100) NOT NULL,
  duration_minutes SMALLINT UNSIGNED NOT NULL,
  fee DECIMAL(10,2) NOT NULL,
  capacity SMALLINT UNSIGNED NOT NULL,
  CONSTRAINT fk_consultation_doctor FOREIGN KEY (doctor_id) REFERENCES doctors(user_id),
  CONSTRAINT chk_consultation_duration CHECK (duration_minutes > 0),
  CONSTRAINT chk_consultation_capacity CHECK (capacity BETWEEN 1 AND 10),
  CONSTRAINT chk_consultation_fee CHECK (fee >= 0),
  UNIQUE KEY uq_doctor_consultation (doctor_id,name)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS doctor_availability (
  availability_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  doctor_id BIGINT NOT NULL,
  consultation_type_id BIGINT NOT NULL,
  day_of_week TINYINT UNSIGNED NOT NULL,
  start_time TIME NOT NULL,
  end_time TIME NOT NULL,
  CONSTRAINT fk_availability_doctor FOREIGN KEY (doctor_id) REFERENCES doctors(user_id),
  CONSTRAINT fk_availability_type FOREIGN KEY (consultation_type_id) REFERENCES consultation_types(consultation_type_id),
  CONSTRAINT chk_availability_day CHECK (day_of_week BETWEEN 0 AND 6),
  CONSTRAINT chk_availability_times CHECK (end_time > start_time),
  INDEX idx_availability_doctor_day (doctor_id,day_of_week)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS doctor_blackouts (
  blackout_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  doctor_id BIGINT NOT NULL,
  blackout_date DATE NOT NULL,
  reason VARCHAR(255) NULL,
  CONSTRAINT fk_blackout_doctor FOREIGN KEY (doctor_id) REFERENCES doctors(user_id),
  INDEX idx_blackout_doctor_date (doctor_id,blackout_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS appointments (
  appointment_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  patient_id BIGINT NOT NULL,
  doctor_id BIGINT NOT NULL,
  slot_id VARCHAR(255) NOT NULL,
  start_time_utc DATETIME NOT NULL,
  status ENUM('CONFIRMED','COMPLETED','CANCELLED_BY_PATIENT','CANCELLED_BY_DOCTOR','NO_SHOW') NOT NULL DEFAULT 'CONFIRMED',
  fee DECIMAL(10,2) NOT NULL,
  reschedule_count TINYINT UNSIGNED NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_appointment_patient FOREIGN KEY (patient_id) REFERENCES patients(user_id),
  CONSTRAINT fk_appointment_doctor FOREIGN KEY (doctor_id) REFERENCES doctors(user_id),
  CONSTRAINT chk_appointment_fee CHECK (fee >= 0),
  CONSTRAINT chk_reschedule_count CHECK (reschedule_count <= 2),
  INDEX idx_appointment_patient_doctor_upcoming (patient_id,doctor_id,status,start_time_utc),
  INDEX idx_appointment_slot (slot_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS refund_ledger (
  refund_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  appointment_id BIGINT NOT NULL,
  refund_percentage DECIMAL(5,2) NOT NULL,
  refund_amount DECIMAL(10,2) NOT NULL,
  deduction_amount DECIMAL(10,2) NOT NULL,
  reason VARCHAR(255) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_refund_appointment FOREIGN KEY (appointment_id) REFERENCES appointments(appointment_id),
  CONSTRAINT chk_refund_percentage CHECK (refund_percentage BETWEEN 0 AND 100),
  CONSTRAINT chk_refund_amount CHECK (refund_amount >= 0 AND deduction_amount >= 0),
  INDEX idx_refund_appointment (appointment_id)
) ENGINE=InnoDB;
