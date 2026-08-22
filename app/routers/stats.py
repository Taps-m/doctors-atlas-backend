from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import User
from app.security import require_role
from app.analysis import compute_stats

router = APIRouter()


@router.get("")
def get_stats(days: int = 17, db: Session = Depends(get_db), current_user: User = Depends(require_role("doctor", "admin"))):
    if not current_user.clinic_id:
        raise HTTPException(status_code=400, detail="This account has no clinic attached")
    return compute_stats(db, current_user.clinic_id, days=days)
