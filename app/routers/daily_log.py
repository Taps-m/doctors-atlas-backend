from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import User, DailyLog
from app.security import get_current_user
from app.schemas import DailyLogRequest, DailyLogOut

router = APIRouter()


@router.post("", response_model=DailyLogOut)
def save_daily_log(
    payload: DailyLogRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Save (or update, if one already exists for that date) today's
    end-of-day totals. Designed to be called once a day, in under a
    minute, from the "Daily Clinic Log" form.
    """
    if not current_user.clinic_id:
        raise HTTPException(status_code=400, detail="This account has no clinic attached")

    log_date = payload.log_date or date.today()

    existing = db.query(DailyLog).filter(
        DailyLog.clinic_id == current_user.clinic_id,
        DailyLog.log_date == log_date,
    ).first()

    if existing:
        existing.new_patients = payload.new_patients
        existing.returning_patients = payload.returning_patients
        existing.total_consultations = payload.total_consultations
        existing.no_shows = payload.no_shows
        existing.revenue = payload.revenue
        existing.new_enquiries = payload.new_enquiries
        db.commit()
        db.refresh(existing)
        return existing

    log = DailyLog(
        clinic_id=current_user.clinic_id,
        log_date=log_date,
        new_patients=payload.new_patients,
        returning_patients=payload.returning_patients,
        total_consultations=payload.total_consultations,
        no_shows=payload.no_shows,
        revenue=payload.revenue,
        new_enquiries=payload.new_enquiries,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@router.get("/{log_date}", response_model=DailyLogOut)
def get_daily_log(log_date: date, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    log = db.query(DailyLog).filter(
        DailyLog.clinic_id == current_user.clinic_id,
        DailyLog.log_date == log_date,
    ).first()
    if not log:
        raise HTTPException(status_code=404, detail="No log entry for that date")
    return log


@router.get("")
def list_daily_logs(
    limit: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(DailyLog)
        .filter(DailyLog.clinic_id == current_user.clinic_id)
        .order_by(DailyLog.log_date.desc())
        .limit(limit)
        .all()
    )
