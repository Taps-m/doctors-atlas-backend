from datetime import datetime, date
from typing import List, Optional, Literal

from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: Literal["admin", "doctor", "staff"] = "doctor"
    clinic_name: Optional[str] = None  # used when role == "doctor" (creates a new clinic)
    clinic_id: Optional[int] = None  # used when role == "staff" (joins an existing clinic)
    avatar_url: Optional[str] = None  # small base64 data URI from the sign-up photo upload


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    name: str


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: str
    clinic_id: Optional[int]
    avatar_url: Optional[str] = None

    class Config:
        from_attributes = True


class AskAdvisorRequest(BaseModel):
    question: str


class AskAdvisorResponse(BaseModel):
    answer: str
    created_at: datetime


class StartActionRequest(BaseModel):
    title: str
    description: Optional[str] = None


class DailyLogRequest(BaseModel):
    """Matches the end-of-day form: new/returning patients, total
    consultations, no-shows, revenue, new enquiries. Defaults to today
    if no date is given."""
    log_date: Optional[date] = None
    new_patients: int = 0
    returning_patients: int = 0
    total_consultations: int = 0
    no_shows: int = 0
    revenue: float = 0
    new_enquiries: int = 0


class DailyLogOut(BaseModel):
    id: int
    log_date: date
    new_patients: int
    returning_patients: int
    total_consultations: int
    no_shows: int
    revenue: float
    new_enquiries: int

    class Config:
        from_attributes = True


class PatientCreate(BaseModel):
    """Payload for adding a new patient from the Patients page."""
    name: str
    phone: Optional[str] = None
    first_visit_at: Optional[datetime] = None


class PatientOut(BaseModel):
    id: int
    name: str
    phone: Optional[str] = None
    first_visit_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AppointmentCreate(BaseModel):
    """Payload for booking a new appointment from the Appointments page."""
    patient_id: int
    scheduled_at: datetime


class AppointmentStatusUpdate(BaseModel):
    status: Literal["scheduled", "completed", "no_show", "cancelled"]


class UpdateProfileRequest(BaseModel):
    """Payload for the Settings > Profile form. Either field can be
    sent alone - only the fields provided are changed."""
    name: Optional[str] = None
    avatar_url: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UpdateClinicRequest(BaseModel):
    """Both fields are optional - the doctor can rename her clinic, add
    or remove a logo, or do one without the other. Nothing is required
    of her."""
    name: Optional[str] = None
    logo_url: Optional[str] = None


class ClinicOut(BaseModel):
    id: int
    name: str
    logo_url: Optional[str] = None
    booking_slug: Optional[str] = None
    booking_enabled: bool = False

    class Config:
        from_attributes = True


# ---------- Patient booking: the doctor's own settings ----------

class BookingWindow(BaseModel):
    start: str  # "HH:MM"
    end: str


class BookingSettingsOut(BaseModel):
    booking_slug: Optional[str] = None
    booking_enabled: bool = False
    slot_minutes: int = 30
    booking_hours: dict = {}
    # Extra inbox copied on booking notifications. The doctor's own
    # account email always receives them and isn't configurable here.
    notify_email: Optional[str] = None
    blocked: List["BlockedSlotOut"] = []


class UpdateBookingSettingsRequest(BaseModel):
    """Every field optional - she can change her hours without touching
    her link, or turn booking off without losing her setup."""
    booking_slug: Optional[str] = None
    booking_enabled: Optional[bool] = None
    slot_minutes: Optional[int] = None
    booking_hours: Optional[dict] = None
    # Empty string clears it.
    notify_email: Optional[str] = None


class BlockSlotRequest(BaseModel):
    block_date: date
    start_time: Optional[str] = None  # None blocks the whole day
    reason: Optional[str] = None


class BlockedSlotOut(BaseModel):
    id: int
    block_date: date
    start_time: Optional[str] = None
    reason: Optional[str] = None

    class Config:
        from_attributes = True


# ---------- Patient booking: the public, unauthenticated side ----------

class PublicClinicOut(BaseModel):
    """Deliberately minimal. A patient sees the clinic's name, its logo
    and how long an appointment lasts - never its id, its invite code,
    its staff, or anything about other patients."""
    name: str
    logo_url: Optional[str] = None
    slot_minutes: int


class PublicDayOut(BaseModel):
    date: date
    slots: List[str]  # free start times as "HH:MM"


class PublicBookingRequest(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    message: Optional[str] = None
    scheduled_at: datetime


class PublicBookingOut(BaseModel):
    ok: bool
    scheduled_at: datetime
    clinic_name: str


BookingSettingsOut.model_rebuild()


class StaffOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: str
    created_at: datetime

    class Config:
        from_attributes = True
