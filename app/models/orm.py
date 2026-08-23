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
    first_visit_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    clinic = relationship("Clinic", back_populates="patients")
    visits = relationship("Visit", back_populates="patient")


class Visit(Base):
    __tablename__ = "visits"

    id = Column(Integer, primary_key=True)
    clinic_id = Column(Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    scheduled_at = Column(TIMESTAMP(timezone=True), nullable=False)
    status = Column(Text, nullable=False)  # completed | no_show | cancelled | scheduled
    revenue = Column(Numeric(10, 2), default=0)
    is_repeat_visit = Column(Boolean, default=False)
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


class AdvisorQuery(Base):
    __tablename__ = "advisor_queries"

    id = Column(Integer, primary_key=True)
    clinic_id = Column(Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
