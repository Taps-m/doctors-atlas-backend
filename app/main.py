from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import CORS_ORIGINS
from app.database import engine, Base
from app import models  # noqa: F401  (registers models with Base.metadata)
from app.routers import (
    auth, stats, advisor, actions, daily_log, patients, appointments, settings,
    booking, public_booking,
)

app = FastAPI(title="Doctors Atlas API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Small, idempotent column additions. create_all() below only creates
# tables that don't exist yet - it never alters an existing one - so a
# new column on an existing table needs an explicit ALTER. Each of
# these uses IF NOT EXISTS, so running them on every startup is safe
# and no manual database step is required when deploying.
COLUMN_MIGRATIONS = [
    "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS logo_url TEXT",
    # Public patient booking.
    "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS booking_slug TEXT",
    "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS booking_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS slot_minutes INTEGER NOT NULL DEFAULT 30",
    "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS booking_hours JSONB",
    "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS notify_email TEXT",
    "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS phone TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_clinics_booking_slug ON clinics (booking_slug)",
    "ALTER TABLE blocked_slots ADD COLUMN IF NOT EXISTS end_time TEXT",
    # See the note on Visit.scheduled_at. Existing rows hold the naive
    # wall-clock value that Postgres labelled UTC, so reading it back
    # AT TIME ZONE 'UTC' returns exactly the time that was booked.
    #
    # Guarded by an explicit type check rather than run blind: this
    # statement executes on every startup, and converting an already
    # converted column would depend on the server's session timezone
    # to be harmless. Appointment times are not something to leave to
    # a default.
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'visits'
              AND column_name = 'scheduled_at'
              AND data_type = 'timestamp with time zone'
        ) THEN
            ALTER TABLE visits
                ALTER COLUMN scheduled_at TYPE TIMESTAMP WITHOUT TIME ZONE
                USING scheduled_at AT TIME ZONE 'UTC';
        END IF;
    END $$;
    """,
    "ALTER TABLE patients ADD COLUMN IF NOT EXISTS email TEXT",
    "ALTER TABLE visits ADD COLUMN IF NOT EXISTS notes TEXT",
    "ALTER TABLE visits ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'clinic'",
]


def backfill_booking_slugs():
    """
    Give every existing clinic a booking slug derived from its name, so
    the doctor never has to invent one. Runs once per clinic - a clinic
    that already has a slug is left completely alone, including one the
    doctor has since edited by hand.
    """
    from app.booking_utils import slugify, unique_slug

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, name FROM clinics WHERE booking_slug IS NULL OR booking_slug = ''")
        ).fetchall()
        if not rows:
            return
        taken = {
            r[0]
            for r in conn.execute(
                text("SELECT booking_slug FROM clinics WHERE booking_slug IS NOT NULL")
            ).fetchall()
        }
        for clinic_id, name in rows:
            slug = unique_slug(slugify(name) or f"clinic-{clinic_id}", taken)
            taken.add(slug)
            conn.execute(
                text("UPDATE clinics SET booking_slug = :s WHERE id = :i"),
                {"s": slug, "i": clinic_id},
            )


@app.on_event("startup")
def on_startup():
    # Creates any tables that don't exist yet. For real schema changes
    # later, switch to Alembic migrations instead of relying on this.
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        for statement in COLUMN_MIGRATIONS:
            conn.execute(text(statement))

    backfill_booking_slugs()


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(stats.router, prefix="/stats", tags=["stats"])
app.include_router(advisor.router, prefix="/advisor", tags=["advisor"])
app.include_router(actions.router, prefix="/actions", tags=["actions"])
app.include_router(daily_log.router, prefix="/daily-log", tags=["daily-log"])
app.include_router(patients.router, prefix="/patients", tags=["patients"])
app.include_router(appointments.router, prefix="/appointments", tags=["appointments"])
app.include_router(settings.router, prefix="/settings", tags=["settings"])
app.include_router(booking.router, prefix="/booking", tags=["booking"])
# No auth on this one by design - it is the patient-facing page.
app.include_router(public_booking.router, prefix="/public/book", tags=["public-booking"])
