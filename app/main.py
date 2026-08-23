from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import CORS_ORIGINS
from app.database import engine, Base
from app import models  # noqa: F401  (registers models with Base.metadata)
from app.routers import auth, stats, advisor, actions, daily_log, patients, appointments, settings

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
]


@app.on_event("startup")
def on_startup():
    # Creates any tables that don't exist yet. For real schema changes
    # later, switch to Alembic migrations instead of relying on this.
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        for statement in COLUMN_MIGRATIONS:
            conn.execute(text(statement))


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
