-- ============================================================
-- MEDISLOT - MYSQL DATABASE SCHEMA
-- Database: MediSlotDB
-- ============================================================

USE MediSlotDB;

-- ============================================================
-- 1. USERS TABLE
-- Purpose: Registration, login and authentication
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT AUTO_INCREMENT PRIMARY KEY,

    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,

    role ENUM('PATIENT', 'DOCTOR', 'ADMIN')
        NOT NULL DEFAULT 'PATIENT',

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP

) ENGINE=InnoDB;


-- ============================================================
-- 2. PATIENTS TABLE
-- Purpose: Store patient-specific information
-- ============================================================

CREATE TABLE IF NOT EXISTS patients (
    patient_id BIGINT AUTO_INCREMENT PRIMARY KEY,

    user_id BIGINT NOT NULL UNIQUE,

    phone VARCHAR(20),
    date_of_birth DATE,
    gender ENUM('MALE', 'FEMALE', 'OTHER'),
    address TEXT,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_patient_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE

) ENGINE=InnoDB;


-- ============================================================
-- 3. DOCTORS TABLE
-- Purpose: Store doctor profiles and professional details
-- ============================================================

CREATE TABLE IF NOT EXISTS doctors (
    doctor_id BIGINT AUTO_INCREMENT PRIMARY KEY,

    user_id BIGINT NOT NULL UNIQUE,

    specialization VARCHAR(100) NOT NULL,
    qualification VARCHAR(255),
    experience_years INT UNSIGNED DEFAULT 0,

    consultation_fee DECIMAL(10,2) NOT NULL DEFAULT 0.00,

    phone VARCHAR(20),
    license_number VARCHAR(100) UNIQUE,
    bio TEXT,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_doctor_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_consultation_fee
        CHECK (consultation_fee >= 0),

    INDEX idx_doctor_specialization (specialization),
    INDEX idx_doctor_active (is_active)

) ENGINE=InnoDB;


-- ============================================================
-- 4. DOCTOR AVAILABILITY TABLE
-- Purpose: Store recurring weekly availability patterns
-- Actual bookable slots are maintained in DynamoDB
-- ============================================================

CREATE TABLE IF NOT EXISTS doctor_availability (
    availability_id BIGINT AUTO_INCREMENT PRIMARY KEY,

    doctor_id BIGINT NOT NULL,

    day_of_week TINYINT UNSIGNED NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,

    slot_duration_minutes INT UNSIGNED NOT NULL DEFAULT 30,
    slot_capacity INT UNSIGNED NOT NULL DEFAULT 1,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_availability_doctor
        FOREIGN KEY (doctor_id)
        REFERENCES doctors(doctor_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_day_of_week
        CHECK (day_of_week BETWEEN 0 AND 6),

    CONSTRAINT chk_availability_time
        CHECK (end_time > start_time),

    CONSTRAINT chk_slot_duration
        CHECK (slot_duration_minutes > 0),

    CONSTRAINT chk_slot_capacity
        CHECK (slot_capacity > 0),

    INDEX idx_availability_doctor_day
        (doctor_id, day_of_week)

) ENGINE=InnoDB;


-- ============================================================
-- 5. APPOINTMENTS TABLE
-- Purpose: Store confirmed appointment records
-- Slot inventory and capacity are managed in DynamoDB
-- ============================================================

CREATE TABLE IF NOT EXISTS appointments (
    appointment_id BIGINT AUTO_INCREMENT PRIMARY KEY,

    patient_id BIGINT NOT NULL,
    doctor_id BIGINT NOT NULL,

    -- References the slotId in DynamoDB slot inventory
    slot_id VARCHAR(255) NOT NULL,

    appointment_start DATETIME NOT NULL,
    appointment_end DATETIME NOT NULL,

    status ENUM(
        'CONFIRMED',
        'COMPLETED',
        'CANCELLED',
        'NO_SHOW'
    ) NOT NULL DEFAULT 'CONFIRMED',

    consultation_fee DECIMAL(10,2) NOT NULL DEFAULT 0.00,

    reason_for_visit TEXT,
    cancellation_reason TEXT,

    cancelled_at DATETIME NULL,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_appointment_patient
        FOREIGN KEY (patient_id)
        REFERENCES patients(patient_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_appointment_doctor
        FOREIGN KEY (doctor_id)
        REFERENCES doctors(doctor_id)
        ON DELETE RESTRICT,

    CONSTRAINT chk_appointment_time
        CHECK (appointment_end > appointment_start),

    CONSTRAINT chk_appointment_fee
        CHECK (consultation_fee >= 0),

    INDEX idx_appointment_patient
        (patient_id, appointment_start),

    INDEX idx_appointment_doctor
        (doctor_id, appointment_start),

    INDEX idx_appointment_slot
        (slot_id),

    INDEX idx_appointment_status
        (status)

) ENGINE=InnoDB;


-- ============================================================
-- 6. REFUND LEDGER TABLE
-- Purpose: Track refunds for cancelled appointments
-- ============================================================

CREATE TABLE IF NOT EXISTS refund_ledger (
    refund_id BIGINT AUTO_INCREMENT PRIMARY KEY,

    appointment_id BIGINT NOT NULL,

    refund_amount DECIMAL(10,2) NOT NULL,

    refund_status ENUM(
        'PENDING',
        'PROCESSING',
        'COMPLETED',
        'FAILED'
    ) NOT NULL DEFAULT 'PENDING',

    refund_reason VARCHAR(255),
    transaction_reference VARCHAR(255),

    requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at DATETIME NULL,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_refund_appointment
        FOREIGN KEY (appointment_id)
        REFERENCES appointments(appointment_id)
        ON DELETE RESTRICT,

    CONSTRAINT chk_refund_amount
        CHECK (refund_amount >= 0),

    INDEX idx_refund_appointment
        (appointment_id),

    INDEX idx_refund_status
        (refund_status)

) ENGINE=InnoDB;


-- ============================================================
-- END OF MEDISLOT DATABASE SCHEMA
-- ============================================================