-- DESTRUCTIVE: DEV/TEST ONLY. BACK UP ALL DATA FIRST.
-- This file is NOT executed automatically by CloudFormation or db_init.
-- Run only after explicitly approving permanent loss of existing MediSlot records.
-- Existing data in all eight legacy tables will be deleted.
DROP TABLE IF EXISTS refund_ledger;
DROP TABLE IF EXISTS appointments;
DROP TABLE IF EXISTS doctor_availability;
DROP TABLE IF EXISTS doctor_blackouts;
DROP TABLE IF EXISTS consultation_types;
DROP TABLE IF EXISTS patients;
DROP TABLE IF EXISTS doctors;
DROP TABLE IF EXISTS users;
-- Afterwards invoke the db_init Lambda to create the finalized eight tables.
