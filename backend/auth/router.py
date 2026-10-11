"""Routes auth: register, login, me."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from auth.email_utils import is_email_configured, send_reset_email
from auth.schemas import (
    AuthResponse,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    UserCreate,
    UserLogin,
    UserOut,
)
from auth.service import (
    authenticate_user,
    create_access_token,
    create_reset_token,
    decode_reset_token,
    register_user,
    set_password,
)
from config import APP_PUBLIC_URL
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


@router.post("/forgot-password")
async def forgot_password(data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Luôn trả message chung — không tiết lộ email có tồn tại trong hệ thống hay không."""
    generic = {"message": "Nếu email tồn tại, một link đặt lại mật khẩu đã được gửi."}

    user = db.query(User).filter(User.email == data.email).first()
    if not user:
        return generic

    if not is_email_configured():
        raise HTTPException(
            status_code=503,
            detail="Chức năng gửi email chưa được cấu hình (thiếu RESEND_API_KEY).",
        )

    token = create_reset_token(user.email)
    reset_url = f"{APP_PUBLIC_URL}/reset-password?token={token}"
    await send_reset_email(user.email, reset_url)
    return generic


@router.post("/reset-password")
def reset_password(data: ResetPasswordRequest, db: Session = Depends(get_db)):
    email = decode_reset_token(data.token)
    if not email:
        raise HTTPException(status_code=400, detail="Link đặt lại mật khẩu không hợp lệ hoặc đã hết hạn")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Người dùng không tồn tại")

    set_password(db, user, data.password)
    return {"message": "Đặt lại mật khẩu thành công — hãy đăng nhập lại."}
