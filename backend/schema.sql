-- AARAMBH PostgreSQL schema
-- Run once against the Render PostgreSQL database before starting the API.
-- Uses only PostgreSQL; no Supabase tables or storage are required.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    phone_hash TEXT NOT NULL UNIQUE,
    pii TEXT NOT NULL,
    age INTEGER NOT NULL CHECK (age BETWEEN 0 AND 125),
    lang TEXT NOT NULL DEFAULT 'en' CHECK (lang IN ('en', 'hi', 'pa')),
    shared_phone BOOLEAN NOT NULL DEFAULT FALSE,
    missed_before INTEGER NOT NULL DEFAULT 0 CHECK (missed_before >= 0),
    digital_access TEXT NOT NULL DEFAULT 'good',
    is_demo BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS patients_is_demo_idx ON patients (is_demo);

CREATE TABLE IF NOT EXISTS staff (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    staff_code TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('doctor', 'clinician', 'nurse', 'ward', 'pharmacist', 'admin')),
    name TEXT NOT NULL,
    dept TEXT NOT NULL DEFAULT '',
    pw_hash TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS staff_role_idx ON staff (role);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    token_hash TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('patient', 'staff')),
    subject TEXT NOT NULL,
    ip_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expires_at_idx ON sessions (expires_at);
CREATE INDEX IF NOT EXISTS sessions_subject_idx ON sessions (kind, subject);

CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,
    actor TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    entity TEXT NOT NULL DEFAULT '',
    entity_id TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS audit_logs_created_at_idx ON audit_logs (created_at DESC);

CREATE TABLE IF NOT EXISTS followups (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    title TEXT NOT NULL,
    dept TEXT NOT NULL,
    doctor TEXT NOT NULL,
    due DATE NOT NULL,
    time TEXT NOT NULL,
    secret TEXT NOT NULL,
    docs_ready JSONB NOT NULL DEFAULT '[]'::jsonb,
    barriers JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'closed')),
    confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    plan_approved BOOLEAN NOT NULL DEFAULT FALSE,
    plan_approved_by TEXT,
    resched_date DATE,
    resched_reason TEXT,
    callback BOOLEAN NOT NULL DEFAULT FALSE,
    escalated BOOLEAN NOT NULL DEFAULT FALSE,
    outcome TEXT,
    completed_on DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS followups_patient_id_idx ON followups (patient_id);
CREATE INDEX IF NOT EXISTS followups_doctor_idx ON followups (doctor);
CREATE INDEX IF NOT EXISTS followups_due_idx ON followups (due);
CREATE INDEX IF NOT EXISTS followups_status_due_idx ON followups (status, due);

CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    followup_id TEXT NOT NULL REFERENCES followups(id) ON DELETE CASCADE,
    at DATE NOT NULL DEFAULT CURRENT_DATE,
    by_name TEXT NOT NULL,
    mode TEXT NOT NULL,
    result TEXT NOT NULL,
    note_enc TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS contacts_followup_created_idx ON contacts (followup_id, created_at);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    followup_id TEXT NOT NULL REFERENCES followups(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    from_loc TEXT NOT NULL DEFAULT '',
    to_loc TEXT NOT NULL DEFAULT '',
    when_txt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS tasks_followup_id_idx ON tasks (followup_id);
CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks (status);

CREATE TABLE IF NOT EXISTS rx (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    followup_id TEXT NOT NULL REFERENCES followups(id) ON DELETE CASCADE,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    items_enc TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS rx_followup_id_idx ON rx (followup_id);

CREATE TABLE IF NOT EXISTS otps (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    used BOOLEAN NOT NULL DEFAULT FALSE,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0)
);
CREATE INDEX IF NOT EXISTS otps_patient_created_idx ON otps (patient_id, created_at DESC);
CREATE INDEX IF NOT EXISTS otps_expiry_idx ON otps (expires_at);

CREATE TABLE IF NOT EXISTS risk_snapshots (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    followup_id TEXT NOT NULL UNIQUE REFERENCES followups(id) ON DELETE CASCADE,
    features JSONB NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS training_samples (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    followup_id TEXT NOT NULL UNIQUE REFERENCES followups(id) ON DELETE CASCADE,
    features JSONB NOT NULL,
    label SMALLINT NOT NULL CHECK (label IN (0, 1)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Model binaries are stored in PostgreSQL BYTEA so model persistence no longer
-- depends on Supabase Storage or an ephemeral Render filesystem.
CREATE TABLE IF NOT EXISTS ml_models (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name TEXT NOT NULL,
    model_data BYTEA NOT NULL,
    sha256 TEXT NOT NULL,
    threshold DOUBLE PRECISION NOT NULL,
    n_samples INTEGER NOT NULL CHECK (n_samples >= 0),
    active BOOLEAN NOT NULL DEFAULT FALSE,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    trained_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ml_models_active_trained_idx ON ml_models (active, trained_at DESC);

-- Prevent accidental OTP accumulation; expired OTPs can also be pruned by a job.
CREATE INDEX IF NOT EXISTS otps_unused_lookup_idx ON otps (patient_id, used, expires_at DESC);
