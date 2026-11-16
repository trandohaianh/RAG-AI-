"""FastAPI authentication dependencies."""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from auth.models import User
from auth.schemas import CurrentUser
from auth.service import decode_access_token
from database import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def email_verification_required() -> bool:
    """Chỉ bắt buộc khi đã bật cờ VÀ có cấu hình gửi email (nếu không sẽ không ai xác thực được)."""
    import config
    from auth.email_utils import is_email_configured

    return bool(config.REQUIRE_EMAIL_VERIFICATION and is_email_configured())


async def get_current_user_allow_unverified(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token không hợp lệ",
            headers={"WWW-Authenticate": "Bearer"},
        )
    row = (
        db.query(User)
        .filter(
            User.id == int(payload["sub"]),
            User.deleted_at.is_(None),
            User.token_version == payload["version"],
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hiệu lực")
    return CurrentUser(
        id=row.id,
        email=row.email,
        role=row.role,
        subscription_plan=row.subscription_plan or "free",
        email_verified=row.email_verified_at is not None,
    )


async def get_current_user(
    user: CurrentUser = Depends(get_current_user_allow_unverified),
) -> CurrentUser:
    if not user.email_verified and email_verification_required():
        raise HTTPException(
            status_code=403,
            detail="Vui lòng xác thực email trước khi tiếp tục (kiểm tra hộp thư của bạn).",
            headers={"X-Error-Code": "email_not_verified"},
        )
    return user


async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Cần quyền admin")
    return user
