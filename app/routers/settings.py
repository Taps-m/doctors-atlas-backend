from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import User, Clinic
from app.security import get_current_user, require_role, hash_password, verify_password
from app.schemas import (
    UpdateProfileRequest,
    UserOut,
    ChangePasswordRequest,
    UpdateClinicRequest,
    ClinicOut,
    StaffOut,
)

router = APIRouter()


@router.patch("/profile", response_model=UserOut)
def update_profile(
    payload: UpdateProfileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update the logged-in user's own name and/or profile photo."""
    if payload.avatar_url and len(payload.avatar_url) > 500_000:
        raise HTTPException(status_code=400, detail="Photo is too large - please use a smaller image")

    if payload.name is not None:
        if not payload.name.strip():
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        current_user.name = payload.name.strip()

    if payload.avatar_url is not None:
        current_user.avatar_url = payload.avatar_url

    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Change the logged-in user's own password."""
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    current_user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True}


@router.get("/clinic", response_model=ClinicOut)
def get_clinic(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin")),
):
    clinic = db.query(Clinic).filter(Clinic.id == current_user.clinic_id).first()
    if not clinic:
        raise HTTPException(status_code=400, detail="This account has no clinic attached")
    return clinic


@router.patch("/clinic", response_model=ClinicOut)
def update_clinic(
    payload: UpdateClinicRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin")),
):
    """
    Update the clinic's name and/or logo. Both are optional and
    independent - sending only a logo leaves the name untouched, and
    sending an empty logo_url clears it. Nothing here is mandatory:
    a clinic works perfectly well with no logo at all.
    """
    clinic = db.query(Clinic).filter(Clinic.id == current_user.clinic_id).first()
    if not clinic:
        raise HTTPException(status_code=400, detail="This account has no clinic attached")

    if payload.name is not None:
        if not payload.name.strip():
            raise HTTPException(status_code=400, detail="Clinic name cannot be empty")
        clinic.name = payload.name.strip()

    if payload.logo_url is not None:
        if len(payload.logo_url) > 500_000:
            raise HTTPException(status_code=400, detail="Logo is too large - please use a smaller image")
        clinic.logo_url = payload.logo_url or None  # empty string = remove

    db.commit()
    db.refresh(clinic)
    return clinic


@router.get("/staff", response_model=List[StaffOut])
def list_staff(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin")),
):
    """Everyone else in the clinic (staff and any co-doctors/admins) -
    used by the Settings > Team section."""
    return (
        db.query(User)
        .filter(User.clinic_id == current_user.clinic_id, User.id != current_user.id)
        .order_by(User.created_at.asc())
        .all()
    )


@router.delete("/staff/{user_id}", status_code=204)
def remove_staff(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("doctor", "admin")),
):
    target = db.query(User).filter(
        User.id == user_id, User.clinic_id == current_user.clinic_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="No such team member in your clinic")
    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="You can't remove your own account here")
    if target.role != "staff":
        raise HTTPException(status_code=400, detail="Only staff accounts can be removed here")

    db.delete(target)
    db.commit()
    return None
