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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import Clinic, Patient, Visit, User
from app.schemas import PublicConsultOut
from app.booking_utils import clinic_now, consult_window

router = APIRouter()

# Statuses where the appointment is still going to happen. A cancelled
# visit keeps its page, but says so instead of offering a way in.
JOINABLE_STATUSES = ("scheduled",)


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

    opens_at, closes_at = consult_window(visit.scheduled_at, clinic.slot_minutes or 30)
    now = clinic_now()

    join_open = (
        visit.status in JOINABLE_STATUSES
        and opens_at <= now <= closes_at
        and bool((clinic.consult_room_url or "").strip())
    )

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
    )
