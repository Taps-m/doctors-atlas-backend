from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import User, Clinic
from app.schemas import RegisterRequest, TokenResponse, UserOut
from app.security import hash_password, verify_password, create_access_token, get_current_user

router = APIRouter()


@router.post("/register", response_model=TokenResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists")

    if payload.avatar_url and len(payload.avatar_url) > 500_000:
        raise HTTPException(status_code=400, detail="Photo is too large - please use a smaller image")
    
    clinic_id = None
    if payload.role == "doctor":
        clinic = Clinic(name=payload.clinic_name or f"{payload.name}'s Clinic")
        db.add(clinic)
        db.flush()  # get clinic.id before commit
        clinic_id = clinic.id
    elif payload.role == "staff":
        # Staff join an existing clinic rather than create one - the
        # doctor shares their clinic_id with staff to register with.
        if not payload.clinic_id:
            raise HTTPException(status_code=400, detail="Staff accounts must provide the clinic_id to join")
        clinic = db.query(Clinic).filter(Clinic.id == payload.clinic_id).first()
        if not clinic:
            raise HTTPException(status_code=404, detail="No clinic found with that clinic_id")
        clinic_id = clinic.id

    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role,
        clinic_id=clinic_id,
        avatar_url=payload.avatar_url,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user)
    return TokenResponse(access_token=token, role=user.role, name=user.name)


@router.post("/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # OAuth2PasswordRequestForm sends "username" - we treat that as the email.
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    token = create_access_token(user)
    return TokenResponse(access_token=token, role=user.role, name=user.name)


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user
