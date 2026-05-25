"""Routes auth: register, login, me."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from auth.schemas import AuthResponse, UserCreate, UserLogin, UserOut
from auth.service import authenticate_user, create_access_token, register_user
from database import get_db
from auth.models import User
from auth.schemas import CurrentUser
from dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse)
def register(data: UserCreate, db: Session = Depends(get_db)):
    try:
        user = register_user(db, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    token = create_access_token(user.email)
    return AuthResponse(user=UserOut.model_validate(user), access_token=token)


@router.post("/login", response_model=AuthResponse)
def login(data: UserLogin, db: Session = Depends(get_db)):
    user = authenticate_user(db, data.email, data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu sai")
    token = create_access_token(user.email)
    return AuthResponse(user=UserOut.model_validate(user), access_token=token)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(User).filter(User.id == user.id).first()
    if not row:
        raise HTTPException(status_code=401, detail="Người dùng không tồn tại")
    return UserOut.model_validate(row)
