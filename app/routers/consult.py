"""
The patient's own consultation page.

Unauthenticated by design: the patient holds an unguessable token and
that is the only credential this needs. The token is scoped to one
appointment, so it stops being useful the moment that appointment is
over.

The one rule worth stating plainly: the clinic's meeting link is NOT
returned unless the join window is open. Her Google Meet room is a
standing address - if Atlas handed it out at booking time, a forwarded
confirmation would let anyone knock on her door at midnight. Wrapping
it in a per-appointment page, revealed on a timer, is the whole point
of this module existing rather than just emailing her Meet link.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import Clinic, Patient, Visit, User
from app.schemas import (
    PublicConsultOut,
    WaitingRoomOut,
    WaitingRoomJoin,
    WaitingRoomJoined,
)
from app.booking_utils import (
    clinic_now,
    consult_window,
    new_consult_token,
    new_queue_code,
)

router = APIRouter()

# Statuses where the appointment is still going to happen. A cancelled
# visit keeps its page, but says so instead of offering a way in.
JOINABLE_STATUSES = ("scheduled",)

# How long an admitted walk-in stays joinable. Long enough that a
# dropped connection can be re-joined, short enough that the page does
# not stay live all day.
ADMITTED_MINUTES = 120


def _doctor_for(db: Session, clinic_id: int) -> User | None:
    """
    Whose name appears on the page. A clinic can have several accounts;
    the patient only needs to know they are seeing a doctor, so take
    the first one by id for stability rather than the newest.
    """
    return (
        db.query(User)
        .filter(User.clinic_id == clinic_id, User.role.in_(("doctor", "admin")))
        .order_by(User.id.asc())
        .first()
    )


@router.get("/{token}", response_model=PublicConsultOut)
def consult_page(token: str, db: Session = Depends(get_db)):
    # Length guard before touching the database: a token is a fixed
    # shape, so anything wildly off is noise rather than a lookup.
    if not token or len(token) > 128:
        raise HTTPException(status_code=404, detail="This consultation link isn't valid")

    visit = db.query(Visit).filter(Visit.consult_token == token).first()
    if not visit:
        raise HTTPException(status_code=404, detail="This consultation link isn't valid")

    clinic = db.query(Clinic).filter(Clinic.id == visit.clinic_id).first()
    patient = db.query(Patient).filter(Patient.id == visit.patient_id).first()
    if not clinic or not patient:
        raise HTTPException(status_code=404, detail="This consultation link isn't valid")

    now = clinic_now()
    has_room = bool((clinic.consult_room_url or "").strip())
    waiting = visit.waiting_since is not None and visit.admitted_at is None

    if visit.admitted_at is not None:
        # A walk-in the doctor has called in. The queue, not the clock,
        # decides when this one opens.
        opens_at = visit.admitted_at
        closes_at = visit.admitted_at + timedelta(minutes=ADMITTED_MINUTES)
    else:
        opens_at, closes_at = consult_window(visit.scheduled_at, clinic.slot_minutes or 30)

    if waiting:
        join_open = False
    else:
        join_open = (
            visit.status in JOINABLE_STATUSES
            and opens_at <= now <= closes_at
            and has_room
        )

    # Where they stand in the queue, counting only people still waiting
    # who arrived before them.
    queue_position = None
    if waiting:
        ahead = (
            db.query(Visit)
            .filter(
                Visit.clinic_id == clinic.id,
                Visit.waiting_since.isnot(None),
                Visit.admitted_at.is_(None),
                Visit.status == "scheduled",
                Visit.waiting_since < visit.waiting_since,
            )
            .count()
        )
        queue_position = ahead + 1

    doctor = _doctor_for(db, clinic.id)

    return PublicConsultOut(
        clinic_name=clinic.name,
        logo_url=clinic.logo_url,
        clinic_phone=clinic.phone,
        doctor_name=doctor.name if doctor else None,
        doctor_reg_no=clinic.doctor_reg_no,
        patient_name=patient.name,
        scheduled_at=visit.scheduled_at,
        status=visit.status,
        join_open=join_open,
        opens_at=opens_at,
        closes_at=closes_at,
        # Only when the window is open. See the module docstring.
        room_url=clinic.consult_room_url if join_open else None,
        waiting=waiting,
        queue_position=queue_position,
        queue_code=visit.queue_code,
    )


# --------------------------------------------------------------------
# The walk-in waiting room.
#
# One permanent address per clinic, fit to print on a card. The patient
# arrives whenever, gives a name, and waits; the doctor calls them in
# one at a time. Nothing has to be sent to anybody, which is the whole
# point - no SMS bill, no WhatsApp API, no link to chase.
# --------------------------------------------------------------------


# The bare "/room" address, with no clinic named. Resolves to the
# lowest-numbered clinic offering video consultations, which is stable
# for the life of that clinic - the same trick the bare booking URL
# uses, so the front page can carry a link before anyone has typed a
# slug.
DEFAULT_ROOM_SLUG = "_default"


def _room_clinic(db: Session, slug: str) -> Clinic:
    if slug == DEFAULT_ROOM_SLUG:
        clinic = (
            db.query(Clinic)
            .filter(Clinic.online_consult_enabled.is_(True))
            .order_by(Clinic.id.asc())
            .first()
        )
    else:
        clinic = db.query(Clinic).filter(Clinic.booking_slug == slug).first()

    if not clinic or not clinic.online_consult_enabled:
        raise HTTPException(status_code=404, detail="This consultation page isn't available")
    return clinic


def _waiting_count(db: Session, clinic_id: int) -> int:
    return (
        db.query(Visit)
        .filter(
            Visit.clinic_id == clinic_id,
            Visit.waiting_since.isnot(None),
            Visit.admitted_at.is_(None),
            Visit.status == "scheduled",
        )
        .count()
    )


@router.get("/room/{slug}", response_model=WaitingRoomOut)
def waiting_room(slug: str, db: Session = Depends(get_db)):
    clinic = _room_clinic(db, slug)
    doctor = _doctor_for(db, clinic.id)
    return WaitingRoomOut(
        clinic_name=clinic.name,
        logo_url=clinic.logo_url,
        clinic_phone=clinic.phone,
        doctor_name=doctor.name if doctor else None,
        doctor_reg_no=clinic.doctor_reg_no,
        open=bool((clinic.consult_room_url or "").strip()),
        waiting_count=_waiting_count(db, clinic.id),
    )


@router.post("/room/{slug}/join", response_model=WaitingRoomJoined, status_code=201)
def join_waiting_room(slug: str, payload: WaitingRoomJoin, db: Session = Depends(get_db)):
    clinic = _room_clinic(db, slug)
    if not (clinic.consult_room_url or "").strip():
        raise HTTPException(status_code=400, detail="This clinic isn't taking video consultations right now")

    name = payload.name.strip()
    phone = (payload.phone or "").strip() or None
    if not name:
        raise HTTPException(status_code=400, detail="Please enter your name")

    # A queue is not a patient record. Match an existing one on phone
    # so repeat visitors stay a single person, but never merge two
    # strangers who happen to share a name.
    patient = None
    if phone:
        patient = (
            db.query(Patient)
            .filter(Patient.clinic_id == clinic.id, Patient.phone == phone)
            .first()
        )
    if not patient:
        patient = Patient(clinic_id=clinic.id, name=name, phone=phone)
        db.add(patient)
        db.flush()

    now = clinic_now()
    visit = Visit(
        clinic_id=clinic.id,
        patient_id=patient.id,
        scheduled_at=now,
        status="scheduled",
        mode="online",
        consult_token=new_consult_token(),
        queue_code=new_queue_code(),
        waiting_since=now,
        source="online",
    )
    db.add(visit)
    db.commit()
    db.refresh(visit)
    return WaitingRoomJoined(token=visit.consult_token)
