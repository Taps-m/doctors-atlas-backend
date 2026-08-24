"""
The public, UNAUTHENTICATED patient booking endpoints.

Everything a patient can reach lives here, and the rules are
deliberately narrow:

  * A patient can do exactly two things - see which slots are free, and
    book one. There is no way to list, amend or cancel anyone's
    appointment, and no way to reach any other part of the API.
  * Nothing here returns patient data. The availability response says
    only which times are free; it never says who holds the others, or
    even how many there are.
  * The clinic is addressed by its public slug, never by its id, so a
    patient holding a booking link cannot derive the clinic's staff
    invite code from it.
  * Booking is refused unless the doctor has explicitly switched it on.
"""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import Clinic, Patient, Visit, BlockedSlot, User
from app.email_client import send_booking_notification
from app.schemas import (
    PublicClinicOut,
    PublicDayOut,
    PublicBookingRequest,
    PublicBookingOut,
)
from app.booking_utils import generate_day_slots, MAX_DAYS_AHEAD

router = APIRouter()

# How many appointments one phone number may hold at once. Stops a
# public page being used to fill the whole calendar, without getting in
# a real family's way.
MAX_UPCOMING_PER_PHONE = 2

# Bounds on what a patient can type, so the form can't be used to push
# large payloads into the clinic's records.
MAX_NAME = 80
MAX_PHONE = 20
MAX_EMAIL = 120
MAX_MESSAGE = 300

# Statuses that still occupy a slot. A cancelled visit frees its time.
ACTIVE_STATUSES = ("scheduled", "completed")


def _normalize_phone(raw: str) -> str:
    """
    Reduce a typed phone number to a comparable identity, so the booking
    limit isn't trivially bypassed by typing the same number a different
    way. "+91 98765 43210", "098765 43210" and "9876543210" must all
    resolve to the same key.

    Taking the last 10 digits handles the country code and the leading
    zero in one step. That is right for Indian numbers, which are 10
    digits; a longer international number would compare on its last 10,
    which is a deliberate trade in favour of catching real duplicates.
    """
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


# The slug reserved for the short, bare "/booking" URL. It resolves to
# the clinic that owns that path - the lowest-numbered one with booking
# switched on, which is stable for the life of that clinic. Every clinic
# also always has its own /book/<slug>, so a second clinic signing up
# can never take this one's link away.
DEFAULT_SLUG = "_default"


def _open_clinic(db: Session, slug: str) -> Clinic:
    if slug == DEFAULT_SLUG:
        clinic = (
            db.query(Clinic)
            .filter(Clinic.booking_enabled.is_(True))
            .order_by(Clinic.id.asc())
            .first()
        )
    else:
        clinic = db.query(Clinic).filter(Clinic.booking_slug == slug).first()

    # Same message either way: an unknown slug and a clinic that hasn't
    # switched booking on look identical from outside, so this can't be
    # used to discover which clinics exist.
    if not clinic or not clinic.booking_enabled:
        raise HTTPException(status_code=404, detail="This booking page isn't available")
    return clinic


@router.get("/{slug}", response_model=PublicClinicOut)
def public_clinic(slug: str, db: Session = Depends(get_db)):
    clinic = _open_clinic(db, slug)
    return PublicClinicOut(
        name=clinic.name,
        logo_url=clinic.logo_url,
        phone=clinic.phone,
        slot_minutes=clinic.slot_minutes or 30,
    )


@router.get("/{slug}/availability", response_model=list[PublicDayOut])
def public_availability(
    slug: str,
    days: int = 14,
    db: Session = Depends(get_db),
):
    """Free slots for the next `days` days, starting today."""
    clinic = _open_clinic(db, slug)
    days = max(1, min(days, MAX_DAYS_AHEAD))

    today = date.today()
    last = today + timedelta(days=days - 1)
    slot_minutes = clinic.slot_minutes or 30

    # Everything already taken in the window, in one query.
    taken = set()
    visits = (
        db.query(Visit.scheduled_at)
        .filter(
            Visit.clinic_id == clinic.id,
            Visit.status.in_(ACTIVE_STATUSES),
            Visit.scheduled_at >= datetime.combine(today, datetime.min.time()),
            Visit.scheduled_at <= datetime.combine(last, datetime.max.time()),
        )
        .all()
    )
    for (when,) in visits:
        if when:
            taken.add(when.strftime("%Y-%m-%d %H:%M"))

    # Blocks: whole days and individual slots.
    blocked_days = set()
    blocked_slots = set()
    for b in (
        db.query(BlockedSlot)
        .filter(
            BlockedSlot.clinic_id == clinic.id,
            BlockedSlot.block_date >= today,
            BlockedSlot.block_date <= last,
        )
        .all()
    ):
        if b.start_time:
            blocked_slots.add(f"{b.block_date.isoformat()} {b.start_time}")
        else:
            blocked_days.add(b.block_date)

    now = datetime.now()
    out = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        if day in blocked_days:
            out.append(PublicDayOut(date=day, slots=[]))
            continue

        free = []
        for slot in generate_day_slots(day, clinic.booking_hours or {}, slot_minutes):
            key = slot.strftime("%Y-%m-%d %H:%M")
            if key in taken or key in blocked_slots:
                continue
            if slot <= now:
                continue  # today's slots that have already passed
            free.append(slot.strftime("%H:%M"))
        out.append(PublicDayOut(date=day, slots=free))

    return out


@router.post("/{slug}/book", response_model=PublicBookingOut, status_code=201)
def public_book(
    slug: str,
    payload: PublicBookingRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    clinic = _open_clinic(db, slug)
    slot_minutes = clinic.slot_minutes or 30

    name = (payload.name or "").strip()
    phone_raw = (payload.phone or "").strip()
    digits = _normalize_phone(phone_raw)
    email = (payload.email or "").strip() or None
    message = (payload.message or "").strip() or None

    if not name:
        raise HTTPException(status_code=400, detail="Please enter the patient's name")
    if len(name) > MAX_NAME:
        raise HTTPException(status_code=400, detail="That name is too long")
    if len(digits) < 7 or len(phone_raw) > MAX_PHONE:
        raise HTTPException(status_code=400, detail="Please enter a valid phone number")
    if email and len(email) > MAX_EMAIL:
        raise HTTPException(status_code=400, detail="That email address is too long")
    if message and len(message) > MAX_MESSAGE:
        raise HTTPException(
            status_code=400,
            detail=f"Please keep your note under {MAX_MESSAGE} characters",
        )

    # The requested time, as a naive local datetime - matching how
    # appointments booked inside the clinic are already stored.
    when = payload.scheduled_at
    if when.tzinfo is not None:
        when = when.replace(tzinfo=None)
    when = when.replace(second=0, microsecond=0)

    if when <= datetime.now():
        raise HTTPException(status_code=400, detail="That time has already passed")
    if when.date() > date.today() + timedelta(days=MAX_DAYS_AHEAD):
        raise HTTPException(
            status_code=400,
            detail=f"Bookings can only be made up to {MAX_DAYS_AHEAD} days ahead",
        )

    # The slot must be one the clinic's own hours actually produce -
    # a patient cannot invent a time by posting it directly.
    valid_starts = {
        s.strftime("%H:%M")
        for s in generate_day_slots(when.date(), clinic.booking_hours or {}, slot_minutes)
    }
    if when.strftime("%H:%M") not in valid_starts:
        raise HTTPException(status_code=400, detail="That isn't an available appointment time")

    # Blocked?
    day_block = (
        db.query(BlockedSlot)
        .filter(
            BlockedSlot.clinic_id == clinic.id,
            BlockedSlot.block_date == when.date(),
        )
        .all()
    )
    for b in day_block:
        if b.start_time is None or b.start_time == when.strftime("%H:%M"):
            raise HTTPException(status_code=400, detail="That time is no longer available")

    # Already taken? Re-checked here rather than trusting the list the
    # patient's browser was shown, which may be minutes old.
    clash = (
        db.query(Visit)
        .filter(
            Visit.clinic_id == clinic.id,
            Visit.scheduled_at == when,
            Visit.status.in_(ACTIVE_STATUSES),
        )
        .first()
    )
    if clash:
        raise HTTPException(status_code=409, detail="Someone just took that slot - please pick another")

    # Find or create the patient record for this phone number.
    patient = None
    if digits:
        for p in db.query(Patient).filter(Patient.clinic_id == clinic.id).all():
            if _normalize_phone(p.phone) == digits:
                patient = p
                break

    if patient:
        upcoming = (
            db.query(Visit)
            .filter(
                Visit.clinic_id == clinic.id,
                Visit.patient_id == patient.id,
                Visit.status == "scheduled",
                Visit.scheduled_at >= datetime.now(),
            )
            .count()
        )
        if upcoming >= MAX_UPCOMING_PER_PHONE:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"This number already has {MAX_UPCOMING_PER_PHONE} upcoming appointments. "
                    "Please call the clinic if you need another."
                ),
            )
        # Fill in details we didn't have before, without overwriting.
        if email and not patient.email:
            patient.email = email
    else:
        patient = Patient(
            clinic_id=clinic.id,
            name=name,
            phone=phone_raw,
            email=email,
            first_visit_at=when,
        )
        db.add(patient)
        db.flush()

    visit = Visit(
        clinic_id=clinic.id,
        patient_id=patient.id,
        scheduled_at=when,
        status="scheduled",
        revenue=0,
        notes=message,
        source="online",
    )
    db.add(visit)
    db.commit()

    # Tell the clinic, AFTER the booking is safely committed and as a
    # background task. The patient's response is never delayed by the
    # mail server, and a mail failure can't undo a real booking.
    doctor_emails = [
        e for (e,) in db.query(User.email)
        .filter(User.clinic_id == clinic.id, User.role.in_(("doctor", "admin")))
        .all()
    ]
    recipients = doctor_emails + ([clinic.notify_email] if clinic.notify_email else [])

    background.add_task(
        send_booking_notification,
        recipients=recipients,
        clinic_name=clinic.name,
        patient_name=name,
        patient_phone=phone_raw,
        patient_email=email,
        when_text=when.strftime("%A %d %b %Y, %I:%M %p").replace(" 0", " "),
        message_text=message,
    )

    return PublicBookingOut(ok=True, scheduled_at=when, clinic_name=clinic.name)
