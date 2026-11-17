"""FastAPI authentication dependencies."""
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

import config
from auth.models import User
from auth.schemas import CurrentUser
from auth.service import decode_access_token
from database import get_db

# auto_error=False: web console xác thực bằng cookie httpOnly thay vì header Bearer.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def request_token(request: Request, bearer: str | None) -> str | None:
    """Lấy JWT từ header Bearer (mobile, API) hoặc từ cookie phiên (web).

    Cookie được trình duyệt tự gửi kèm, nên với phương thức ghi phải có header chống CSRF:
    một trang khác không đặt được header tuỳ ý nếu không qua CORS preflight (chỉ cho origin
    đã cấu hình), còn form/ảnh/link thì không gửi header được.
    """
    if bearer:
        return bearer
    token = request.cookies.get(config.AUTH_COOKIE_NAME)
    if not token:
        return None
    if request.method.upper() not in _SAFE_METHODS and \
            request.headers.get(config.CSRF_HEADER, "").strip().lower() != config.CSRF_HEADER_VALUE:
        raise HTTPException(status_code=403, detail="Thiếu header chống CSRF")
    return token


def require_csrf_header(request: Request) -> None:
    """Cho route GET có tác dụng phụ tốn tiền (gọi LLM): cookie Lax vẫn được gửi khi điều hướng
    cấp cao nhất, nên một trang lạ mở link lặp lại có thể đốt ngân sách AI ngày của nạn nhân."""
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        return
    if (request.cookies.get(config.AUTH_COOKIE_NAME)
            and request.headers.get(config.CSRF_HEADER, "").strip().lower() != config.CSRF_HEADER_VALUE):
        raise HTTPException(status_code=403, detail="Thiếu header chống CSRF")


def email_verification_required() -> bool:
    """Chỉ bắt buộc khi đã bật cờ VÀ có cấu hình gửi email (nếu không sẽ không ai xác thực được)."""
    import config
    from auth.email_utils import is_email_configured

    return bool(config.REQUIRE_EMAIL_VERIFICATION and is_email_configured())


async def get_current_user_allow_unverified(
    request: Request,
    bearer: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    token = request_token(request, bearer)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
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
