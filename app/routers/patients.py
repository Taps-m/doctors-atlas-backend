from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import User, Patient
from app.security import require_role
from app.schemas import PatientCreate, PatientOut

router = APIRouter()


@router.get("", response_model=List[PatientOut])
def list_patients(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin", "staff")),
):
    if not current_user.clinic_id:
        raise HTTPException(status_code=400, detail="This account has no clinic attached")
    return (
        db.query(Patient)
        .filter(Patient.clinic_id == current_user.clinic_id)
        .order_by(desc(Patient.created_at))
        .all()
    )


@router.post("", response_model=PatientOut)
def create_patient(
    payload: PatientCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin", "staff")),
):
    if not current_user.clinic_id:
        raise HTTPException(status_code=400, detail="This account has no clinic attached")

    patient = Patient(
        clinic_id=current_user.clinic_id,
        name=payload.name,
        phone=payload.phone,
        first_visit_at=payload.first_visit_at,
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


@router.delete("/{patient_id}", status_code=204)
def delete_patient(
    patient_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin")),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.clinic_id == current_user.clinic_id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    db.delete(patient)
    db.commit()
    return None
