-- Doctors Atlas database schema
-- Postgres. Run this once against a fresh database (e.g. Neon.tech).

-- ---------- Clinics ----------
-- One clinic per doctor account, for now (keeps the "same database,
-- scoped per doctor" model simple; multi-doctor clinics can come later).
CREATE TABLE clinics (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Users ----------
-- role: 'admin' (you, full system access), 'doctor' (owns a clinic,
-- sees the dashboard/insights), or 'staff' (data entry only - can
-- submit the daily log for their clinic but not view insights).
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    clinic_id       INTEGER REFERENCES clinics(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('admin', 'doctor', 'staff')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Patients ----------
CREATE TABLE patients (
    id              SERIAL PRIMARY KEY,
    clinic_id       INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    phone           TEXT,
    first_visit_at  DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Visits ----------
-- One row per appointment/visit. status covers no-shows for the
-- no-show-rate stat, and revenue feeds the revenue stat.
CREATE TABLE visits (
    id              SERIAL PRIMARY KEY,
    clinic_id       INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
    patient_id      INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    scheduled_at    TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('completed', 'no_show', 'cancelled', 'scheduled')),
    revenue         NUMERIC(10, 2) DEFAULT 0,
    is_repeat_visit BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Recommended actions ----------
-- What the AI Advisor suggested, and whether the doctor acted on it.
-- started_at / measured_at let us compare metrics before vs after,
-- closing the "Doctor Action -> Measure Result" loop from the diagram.
CREATE TABLE actions (
    id                  SERIAL PRIMARY KEY,
    clinic_id           INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
    title               TEXT NOT NULL,
    description         TEXT,
    status              TEXT NOT NULL DEFAULT 'suggested'
                            CHECK (status IN ('suggested', 'started', 'dismissed', 'measured')),
    baseline_metrics    JSONB,   -- snapshot of key stats at suggestion time
    result_metrics      JSONB,   -- snapshot of key stats after measurement
    started_at          TIMESTAMPTZ,
    measured_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- AI Advisor conversation log ----------
-- Keeps a history of questions asked and Gemini's answers, per clinic.
CREATE TABLE advisor_queries (
    id              SERIAL PRIMARY KEY,
    clinic_id       INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
    question        TEXT NOT NULL,
    answer          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Daily logs ----------
-- The real day-to-day data entry point: at the end of each clinic day,
-- the doctor (or staff) fills one quick form with the day's totals.
-- This is what the dashboard's stats are actually computed from -
-- individual patients/visits rows above are for later, finer-grained
-- tracking, not required for the MVP.
CREATE TABLE daily_logs (
    id                      SERIAL PRIMARY KEY,
    clinic_id               INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
    log_date                DATE NOT NULL,
    new_patients            INTEGER NOT NULL DEFAULT 0,
    returning_patients      INTEGER NOT NULL DEFAULT 0,
    total_consultations     INTEGER NOT NULL DEFAULT 0,
    no_shows                INTEGER NOT NULL DEFAULT 0,
    revenue                 NUMERIC(10, 2) NOT NULL DEFAULT 0,
    new_enquiries           INTEGER NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (clinic_id, log_date)  -- one entry per clinic per day; re-saving updates it
);

CREATE INDEX idx_visits_clinic_scheduled ON visits (clinic_id, scheduled_at);
CREATE INDEX idx_patients_clinic ON patients (clinic_id);
CREATE INDEX idx_actions_clinic ON actions (clinic_id);
CREATE INDEX idx_daily_logs_clinic_date ON daily_logs (clinic_id, log_date);
