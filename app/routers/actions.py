from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import User, Action
from app.security import require_role
from app.analysis import compute_stats
from app.schemas import StartActionRequest

router = APIRouter()


@router.get("")
def list_actions(db: Session = Depends(get_db), current_user: User = Depends(require_role("doctor", "admin"))):
    return (
        db.query(Action)
        .filter(Action.clinic_id == current_user.clinic_id)
        .order_by(Action.created_at.desc())
        .all()
    )


@router.post("/{action_id}/start")
def start_action(action_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role("doctor", "admin"))):
    action = db.query(Action).filter(
        Action.id == action_id, Action.clinic_id == current_user.clinic_id
    ).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    action.status = "started"
    action.started_at = datetime.now(timezone.utc)
    action.baseline_metrics = compute_stats(db, current_user.clinic_id)
    db.commit()
    db.refresh(action)
    return action


@router.post("/{action_id}/measure")
def measure_action(action_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role("doctor", "admin"))):
    """
    Call this once enough time has passed since the action was started.
    Snapshots current stats as result_metrics so the frontend can show
    a clear before/after comparison - the "Measure Result" step.
    """
    action = db.query(Action).filter(
        Action.id == action_id, Action.clinic_id == current_user.clinic_id
    ).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if action.status != "started":
        raise HTTPException(status_code=400, detail="Action must be started before it can be measured")

    action.status = "measured"
    action.measured_at = datetime.now(timezone.utc)
    action.result_metrics = compute_stats(db, current_user.clinic_id)
    db.commit()
    db.refresh(action)
    return action


@router.post("/{action_id}/dismiss")
def dismiss_action(action_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role("doctor", "admin"))):
    action = db.query(Action).filter(
        Action.id == action_id, Action.clinic_id == current_user.clinic_id
    ).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    action.status = "dismissed"
    db.commit()
    db.refresh(action)
    return action
