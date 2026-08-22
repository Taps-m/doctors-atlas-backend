from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import User, Clinic, AdvisorQuery
from app.security import require_role
from app.analysis import compute_stats, compute_data_stage
from app.gemini_client import ask_gemini
from app.schemas import AskAdvisorRequest, AskAdvisorResponse

router = APIRouter()


@router.post("/ask", response_model=AskAdvisorResponse)
def ask_advisor(
    payload: AskAdvisorRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin")),
):
    if not current_user.clinic_id:
        raise HTTPException(status_code=400, detail="This account has no clinic attached")

    clinic = db.query(Clinic).filter(Clinic.id == current_user.clinic_id).first()
    stats = compute_stats(db, current_user.clinic_id)
    stage = compute_data_stage(clinic.created_at)

    answer = ask_gemini(stats, stage, payload.question)

    log = AdvisorQuery(
        clinic_id=current_user.clinic_id,
        question=payload.question,
        answer=answer,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    return AskAdvisorResponse(answer=answer, created_at=log.created_at)


@router.get("/stage")
def get_stage(db: Session = Depends(get_db), current_user: User = Depends(require_role("doctor", "admin"))):
    clinic = db.query(Clinic).filter(Clinic.id == current_user.clinic_id).first()
    if not clinic:
        raise HTTPException(status_code=400, detail="This account has no clinic attached")
    return compute_data_stage(clinic.created_at)
