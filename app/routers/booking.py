"""
The doctor's own booking settings: her public link, her weekly hours,
how long an appointment is, and any days or slots she's blocked.

Everything here is authenticated and restricted to doctor/admin. The
patient-facing side lives in public_booking.py and shares no code path
with this one beyond the helpers in booking_utils.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import User, Clinic, BlockedSlot
from app.security import require_role
from app.schemas import (
    BookingSettingsOut,
    UpdateBookingSettingsRequest,
    BlockSlotRequest,
    BlockedSlotOut,
)
from app.booking_utils import (
    validate_slug,
    validate_booking_hours,
    slugify,
    unique_slug,
    MIN_SLOT_MINUTES,
    MAX_SLOT_MINUTES,
)

router = APIRouter()


def _clinic_for(db: Session, user: User) -> Clinic:
    clinic = db.query(Clinic).filter(Clinic.id == user.clinic_id).first()
    if not clinic:
        raise HTTPException(status_code=400, detail="This account has no clinic attached")
    return clinic


def _blocked_for(db: Session, clinic_id: int) -> List[BlockedSlot]:
    return (
        db.query(BlockedSlot)
        .filter(BlockedSlot.clinic_id == clinic_id)
        .order_by(BlockedSlot.block_date.asc())
        .all()
    )


def _settings_payload(db: Session, clinic: Clinic) -> BookingSettingsOut:
    return BookingSettingsOut(
        booking_slug=clinic.booking_slug,
        booking_enabled=bool(clinic.booking_enabled),
        slot_minutes=clinic.slot_minutes or 30,
        booking_hours=clinic.booking_hours or {},
        notify_email=clinic.notify_email,
        blocked=[BlockedSlotOut.model_validate(b) for b in _blocked_for(db, clinic.id)],
    )


@router.get("", response_model=BookingSettingsOut)
def get_booking_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin")),
):
    clinic = _clinic_for(db, current_user)

    # A clinic created before this feature existed may still have no
    # slug; give it one now rather than showing her an empty field.
    if not clinic.booking_slug:
        taken = {
            s for (s,) in db.query(Clinic.booking_slug).filter(Clinic.booking_slug.isnot(None)).all()
        }
        clinic.booking_slug = unique_slug(slugify(clinic.name) or f"clinic-{clinic.id}", taken)
        db.commit()
        db.refresh(clinic)

    return _settings_payload(db, clinic)


@router.patch("", response_model=BookingSettingsOut)
def update_booking_settings(
    payload: UpdateBookingSettingsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin")),
):
    clinic = _clinic_for(db, current_user)

    if payload.booking_slug is not None:
        try:
            slug = validate_slug(payload.booking_slug)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        clash = (
            db.query(Clinic)
            .filter(Clinic.booking_slug == slug, Clinic.id != clinic.id)
            .first()
        )
        if clash:
            raise HTTPException(
                status_code=400,
                detail="That booking link is already taken - try adding your area or speciality",
            )
        clinic.booking_slug = slug

    if payload.slot_minutes is not None:
        if payload.slot_minutes < MIN_SLOT_MINUTES or payload.slot_minutes > MAX_SLOT_MINUTES:
            raise HTTPException(
                status_code=400,
                detail=f"Appointment length must be between {MIN_SLOT_MINUTES} and {MAX_SLOT_MINUTES} minutes",
            )
        clinic.slot_minutes = payload.slot_minutes

    if payload.booking_hours is not None:
        try:
            clinic.booking_hours = validate_booking_hours(payload.booking_hours)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    if payload.notify_email is not None:
        addr = payload.notify_email.strip()
        if addr and ("@" not in addr or len(addr) > 200 or " " in addr):
            raise HTTPException(status_code=400, detail="That doesn't look like a valid email address")
        clinic.notify_email = addr or None

    if payload.booking_enabled is not None:
        # Refuse to switch it on with nothing bookable - otherwise she'd
        # share a link that shows patients an empty calendar.
        if payload.booking_enabled:
            hours = clinic.booking_hours or {}
            if not any(windows for windows in hours.values()):
                raise HTTPException(
                    status_code=400,
                    detail="Set at least one day's opening hours before turning booking on",
                )
            if not clinic.booking_slug:
                raise HTTPException(status_code=400, detail="Set your booking link first")
        clinic.booking_enabled = payload.booking_enabled

    db.commit()
    db.refresh(clinic)
    return _settings_payload(db, clinic)


@router.post("/blocked", response_model=BlockedSlotOut, status_code=201)
def add_blocked(
    payload: BlockSlotRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin")),
):
    clinic = _clinic_for(db, current_user)

    if payload.start_time:
        try:
            hh, mm = payload.start_time.split(":")
            if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                raise ValueError
        except Exception:
            raise HTTPException(status_code=400, detail="Time must look like 09:00")

    existing = (
        db.query(BlockedSlot)
        .filter(
            BlockedSlot.clinic_id == clinic.id,
            BlockedSlot.block_date == payload.block_date,
            BlockedSlot.start_time.is_(None) if payload.start_time is None
            else BlockedSlot.start_time == payload.start_time,
        )
        .first()
    )
    if existing:
        return existing

    block = BlockedSlot(
        clinic_id=clinic.id,
        block_date=payload.block_date,
        start_time=payload.start_time,
        reason=(payload.reason or "").strip() or None,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


@router.delete("/blocked/{block_id}", status_code=204)
def remove_blocked(
    block_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin")),
):
    block = (
        db.query(BlockedSlot)
        .filter(BlockedSlot.id == block_id, BlockedSlot.clinic_id == current_user.clinic_id)
        .first()
    )
    if not block:
        raise HTTPException(status_code=404, detail="No such blocked entry")
    db.delete(block)
    db.commit()
    return None
