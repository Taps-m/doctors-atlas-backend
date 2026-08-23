from sqlalchemy.orm import Session, joinedload

from fastapi import APIRouter, Depends, HTTPException

from app.database import get_db
from app.models.orm import User, Visit, Patient
from app.security import require_role
from app.schemas import AppointmentCreate, AppointmentStatusUpdate

router = APIRouter()


def _serialize(v: Visit) -> dict:
    return {
        "id": v.id,
        "patient_id": v.patient_id,
        "patient_name": v.patient.name if v.patient else "Unknown",
        "scheduled_at": v.scheduled_at,
        "status": v.status,
        "created_at": v.created_at,
    }


@router.get("")
def list_appointments(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin", "staff")),
):
    if not current_user.clinic_id:
        raise HTTPException(status_code=400, detail="This account has no clinic attached")
    visits = (
        db.query(Visit)
        .options(joinedload(Visit.patient))
        .filter(Visit.clinic_id == current_user.clinic_id)
        .order_by(Visit.scheduled_at.asc())
        .all()
    )
    return [_serialize(v) for v in visits]


@router.post("")
def create_appointment(
    payload: AppointmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin", "staff")),
):
    if not current_user.clinic_id:
        raise HTTPException(status_code=400, detail="This account has no clinic attached")

    patient = (
        db.query(Patient)
        .filter(Patient.id == payload.patient_id, Patient.clinic_id == current_user.clinic_id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    visit = Visit(
        clinic_id=current_user.clinic_id,
        patient_id=patient.id,
        scheduled_at=payload.scheduled_at,
        status="scheduled",
    )
    db.add(visit)
    db.commit()
    db.refresh(visit)
    visit.patient = patient
    return _serialize(visit)


@router.patch("/{appointment_id}/status")
def update_appointment_status(
    appointment_id: int,
    payload: AppointmentStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin", "staff")),
):
    visit = (
        db.query(Visit)
        .options(joinedload(Visit.patient))
        .filter(Visit.id == appointment_id, Visit.clinic_id == current_user.clinic_id)
        .first()
    )
    if not visit:
        raise HTTPException(status_code=404, detail="Appointment not found")
    visit.status = payload.status
    db.commit()
    db.refresh(visit)
    return _serialize(visit)


@router.delete("/{appointment_id}", status_code=204)
def delete_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin")),
):
    visit = (
        db.query(Visit)
        .filter(Visit.id == appointment_id, Visit.clinic_id == current_user.clinic_id)
        .first()
    )
    if not visit:
        raise HTTPException(status_code=404, detail="Appointment not found")
    db.delete(visit)
    db.commit()
    return None
