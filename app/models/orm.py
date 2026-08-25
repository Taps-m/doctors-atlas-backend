from sqlalchemy import (
    Column, Integer, String, Text, Boolean, ForeignKey, TIMESTAMP, Numeric, JSON, Date, UniqueConstraint
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class Clinic(Base):
    __tablename__ = "clinics"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    # Optional - a small base64 data URI uploaded from Settings. The
    # clinic works perfectly well without one; the UI falls back to a
    # generic icon.
    logo_url = Column(Text)

    # ---------- Public patient booking ----------
    # The clinic's handle in its public booking URL (/book/<slug>).
    # Derived from the name once, then only changed deliberately -
    # changing it breaks links already shared with patients. Note this
    # is NOT the staff invite code: a patient holding the booking link
    # must never be able to join the clinic with it.
    booking_slug = Column(Text, unique=True)
    # Off until the doctor has set her hours and turned it on, so a
    # slug can never resolve to a page offering slots she never chose.
    booking_enabled = Column(Boolean, nullable=False, default=False)
    # Appointment length in minutes; slots are generated on this grid.
    slot_minutes = Column(Integer, nullable=False, default=30)
    # Weekly opening hours, keyed by Python weekday (0=Monday .. 6=Sunday):
    #   {"0": [{"start": "10:00", "end": "13:00"}, ...], "6": []}
    # A missing or empty list means closed that day.
    booking_hours = Column(JSON)
    # Optional shared inbox (reception@, front desk) that also receives
    # booking notifications alongside the doctor's own account email.
    notify_email = Column(Text)
    # Shown to patients on the public booking page, and - crucially -
    # on its error state, so someone who can't book online still has a
    # way to reach the clinic.
    phone = Column(Text)

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    users = relationship("User", back_populates="clinic")
    patients = relationship("Patient", back_populates="clinic")
    visits = relationship("Visit", back_populates="clinic")
    actions = relationship("Action", back_populates="clinic")
    daily_logs = relationship("DailyLog", back_populates="clinic")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    clinic_id = Column(Integer, ForeignKey("clinics.id", ondelete="SET NULL"))
    name = Column(Text, nullable=False)
    email = Column(Text, nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    role = Column(Text, nullable=False)  # 'admin' or 'doctor'
    avatar_url = Column(Text)  # small base64 data URI, uploaded from the browser
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    clinic = relationship("Clinic", back_populates="users")


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True)
    clinic_id = Column(Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    phone = Column(Text)
    email = Column(Text)  # optional; collected on the public booking form
    first_visit_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    clinic = relationship("Clinic", back_populates="patients")
    visits = relationship("Visit", back_populates="patient")


class Visit(Base):
    __tablename__ = "visits"

    id = Column(Integer, primary_key=True)
    clinic_id = Column(Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    # Naive on purpose: an appointment is a wall-clock time at the
    # clinic ("11:00 on Tuesday"), not an instant on a global
    # timeline. Stored with a timezone, Postgres filed a naive 11:00
    # as 11:00 UTC and browsers rendered it as 16:30 IST. Everything
    # that touches this column - slot generation, blocked ranges,
    # clash checks, datetime.now() - works in naive local time, and
    # the column now matches.
    scheduled_at = Column(TIMESTAMP(timezone=False), nullable=False)
    status = Column(Text, nullable=False)  # completed | no_show | cancelled | scheduled
    revenue = Column(Numeric(10, 2), default=0)
    is_repeat_visit = Column(Boolean, default=False)
    # Whatever the patient wanted the doctor to know, from the public
    # booking form. Free text, deliberately short.
    notes = Column(Text)
    # "clinic" (booked by the doctor or staff) or "online" (booked by a
    # patient through the public page).
    source = Column(Text, nullable=False, default="clinic")
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    clinic = relationship("Clinic", back_populates="visits")
    patient = relationship("Patient", back_populates="visits")


class Action(Base):
    __tablename__ = "actions"

    id = Column(Integer, primary_key=True)
    clinic_id = Column(Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False)
    title = Column(Text, nullable=False)
    description = Column(Text)
    status = Column(Text, nullable=False, default="suggested")
    baseline_metrics = Column(JSON)
    result_metrics = Column(JSON)
    started_at = Column(TIMESTAMP(timezone=True))
    measured_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    clinic = relationship("Clinic", back_populates="actions")


class DailyLog(Base):
    """
    One row per clinic per day - the quick end-of-day form (new patients,
    returning patients, total consultations, no-shows, revenue, new
    enquiries). This is the primary data source the dashboard stats and
    AI Advisor are computed from.
    """
    __tablename__ = "daily_logs"
    __table_args__ = (UniqueConstraint("clinic_id", "log_date", name="uq_daily_logs_clinic_date"),)

    id = Column(Integer, primary_key=True)
    clinic_id = Column(Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False)
    log_date = Column(Date, nullable=False)
    new_patients = Column(Integer, nullable=False, default=0)
    returning_patients = Column(Integer, nullable=False, default=0)
    total_consultations = Column(Integer, nullable=False, default=0)
    no_shows = Column(Integer, nullable=False, default=0)
    revenue = Column(Numeric(10, 2), nullable=False, default=0)
    new_enquiries = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

    clinic = relationship("Clinic", back_populates="daily_logs")


class BlockedSlot(Base):
    """
    Times the clinic is NOT available, on top of the weekly hours -
    holidays, leave, a blocked afternoon.

    Three shapes, in the order the doctor thinks about them:
      start NULL, end NULL -> the whole day is off
      start set,  end set  -> off from start up to (not including)
                              end, e.g. an afternoon 13:00 -> 18:00
      start set,  end NULL -> that one slot only. This is the
                              pre-range shape; rows created before
                              part-day blocking still look like this
                              and must keep working.
    """
    __tablename__ = "blocked_slots"

    id = Column(Integer, primary_key=True)
    clinic_id = Column(Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False)
    block_date = Column(Date, nullable=False)
    start_time = Column(Text)  # "HH:MM", or NULL for the whole day
    end_time = Column(Text)    # "HH:MM", exclusive; NULL = single slot
    reason = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class AdvisorQuery(Base):
    __tablename__ = "advisor_queries"

    id = Column(Integer, primary_key=True)
    clinic_id = Column(Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
