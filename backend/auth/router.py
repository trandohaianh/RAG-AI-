"""Đăng ký, đăng nhập (mật khẩu + Google), xác thực email và khôi phục mật khẩu."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from auth.session_cookie import clear_session_cookie, set_session_cookie, wants_cookie

from auth import google as google_auth
from auth import ratelimit
from auth.email_utils import is_email_configured, send_reset_email, send_verification_email
from auth.models import User
from auth.schemas import (
    AuthResponse,
    CurrentUser,
    ForgotPasswordRequest,
    GoogleLoginRequest,
    ResetPasswordRequest,
    UserCreate,
    UserLogin,
    UserOut,
    VerifyEmailRequest,
)
from auth.service import (
    authenticate_user,
    create_access_token,
    create_email_verification_token,
    create_reset_token,
    find_active_user_by_email,
    login_with_google,
    register_user,
    reset_password_with_token,
    verify_email_with_token,
)
from config import APP_PUBLIC_URL
from database import get_db
from dependencies import email_verification_required, get_current_user_allow_unverified

router = APIRouter(prefix="/auth", tags=["auth"])
_background: set = set()


def _issue(request: Request, response: Response, user: User, *, is_new_user: bool = False) -> AuthResponse:
    """Cấp phiên: web (X-Auth-Mode: cookie) nhận cookie httpOnly và body KHÔNG chứa token;
    mobile/API nhận token trong body như trước."""
    token = create_access_token(user)
    if wants_cookie(request):
        set_session_cookie(response, token)
        token = ""
    return AuthResponse(user=UserOut.from_user(user), access_token=token, is_new_user=is_new_user)


def _auth_event(event: str, result: str) -> None:
    try:
        from metrics import AUTH_EVENTS

        AUTH_EVENTS.labels(event=event, result=result).inc()
    except Exception:
        pass


def _send_in_background(coro) -> None:
    """Gửi email không chặn response (Resend có thể chậm vài giây)."""
    task = asyncio.get_running_loop().create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


def _queue_verification(user: User) -> None:
    if not is_email_configured() or user.email_verified_at is not None:
        return
    token = create_email_verification_token(user)
    _send_in_background(send_verification_email(user.email, f"{APP_PUBLIC_URL}/verify-email?token={token}"))


@router.get("/config")
def auth_config():
    """Frontend đọc để biết nên hiện nút Google / màn hình xác thực email hay không."""
    from config import GOOGLE_CLIENT_IDS

    return {
        "google_client_id": GOOGLE_CLIENT_IDS[0] if GOOGLE_CLIENT_IDS else None,
        "email_verification_required": email_verification_required(),
        "password_reset_enabled": is_email_configured(),
    }


@router.post("/register", response_model=AuthResponse)
async def register(data: UserCreate, request: Request, response: Response, db: Session = Depends(get_db)):
    ratelimit.hit("register:ip", ratelimit.client_ip(request))
    try:
        user = register_user(db, data)
    except ValueError as exc:
        _auth_event("register", "rejected")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _auth_event("register", "ok")
    _queue_verification(user)
    return _issue(request, response, user, is_new_user=True)


@router.post("/login", response_model=AuthResponse)
def login(data: UserLogin, request: Request, response: Response, db: Session = Depends(get_db)):
    ratelimit.hit("login:ip", ratelimit.client_ip(request))
    ratelimit.hit("login:email", data.email)
    user = authenticate_user(db, data.email, data.password)
    if not user:
        _auth_event("login", "failed")
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu sai")
    # Đăng nhập đúng → xoá bộ đếm theo email để người dùng thật không bị khoá oan.
    ratelimit.reset("login:email", data.email)
    _auth_event("login", "ok")
    return _issue(request, response, user)


@router.post("/google", response_model=AuthResponse)
async def google_login(data: GoogleLoginRequest, request: Request, response: Response,
                       db: Session = Depends(get_db)):
    ratelimit.hit("google:ip", ratelimit.client_ip(request))
    if not google_auth.is_enabled():
        raise HTTPException(status_code=404, detail="Đăng nhập Google chưa được bật")
    try:
        # Lấy JWKS là I/O mạng → chạy ngoài event loop.
        identity = await asyncio.to_thread(google_auth.verify_id_token, data.credential)
    except google_auth.GoogleTokenError as exc:
        _auth_event("google", "invalid_token")
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    try:
        user, created = login_with_google(db, identity)
    except ValueError as exc:
        _auth_event("google", "rejected")
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    _auth_event("google", "created" if created else "ok")
    return _issue(request, response, user, is_new_user=created)


@router.post("/session", response_model=UserOut)
def upgrade_to_cookie_session(
    request: Request,
    response: Response,
    user: CurrentUser = Depends(get_current_user_allow_unverified),
    db: Session = Depends(get_db),
):
    """Chuyển phiên web cũ (token trong localStorage) sang cookie httpOnly một lần.

    Cookie mới GIỮ NGUYÊN hạn của token gốc. Trước đây mỗi lần gọi lại cấp hạn 7 ngày mới, nên
    một token bị lộ có thể được gia hạn mãi mãi (chính sách chủ sản phẩm chọn 2026-11-17).
    """
    from datetime import datetime, timezone

    from auth.service import decode_access_token
    from dependencies import request_token

    row = db.query(User).filter(User.id == user.id, User.deleted_at.is_(None)).first()
    if not row:
        raise HTTPException(status_code=401, detail="Người dùng không tồn tại")
    auth_header = request.headers.get("authorization", "")
    bearer = auth_header[7:] if auth_header.lower().startswith("bearer ") else None
    claims = decode_access_token(request_token(request, bearer) or "") or {}
    expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    remaining = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    if remaining <= 0:
        raise HTTPException(status_code=401, detail="Token đã hết hạn")
    set_session_cookie(response, create_access_token(row, expires_at=expires_at), max_age=remaining)
    return UserOut.from_user(row)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Đăng xuất: xoá cookie VÀ thu hồi mọi token của người dùng (tăng token_version).

    Trước đây chỉ xoá cookie, nên token đã bị lộ vẫn dùng được tới hết hạn. Thu hồi áp dụng cho
    mọi thiết bị (web + mobile) — chính sách chủ sản phẩm chọn 2026-11-17. Chỉ thu hồi khi request
    chứng minh được không phải CSRF (bearer, hoặc cookie kèm header chống CSRF); thiếu header thì
    vẫn xoá cookie của chính trình duyệt như cũ (vô hại), không đụng tới thiết bị khác.
    """
    import config
    from auth.service import decode_access_token
    from sqlalchemy import update

    auth_header = request.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.lower().startswith("bearer ") else None
    if token is None and request.headers.get(config.CSRF_HEADER, "").strip().lower() == config.CSRF_HEADER_VALUE:
        token = request.cookies.get(config.AUTH_COOKIE_NAME)
    claims = decode_access_token(token) if token else None
    if claims:
        db.execute(update(User).where(User.id == int(claims["sub"]), User.token_version == claims["version"])
                   .values(token_version=User.token_version + 1))
        db.commit()
    clear_session_cookie(response)
    return {"message": "Đã đăng xuất"}


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser = Depends(get_current_user_allow_unverified), db: Session = Depends(get_db)):
    row = db.query(User).filter(User.id == user.id, User.deleted_at.is_(None)).first()
    if not row:
        raise HTTPException(status_code=401, detail="Người dùng không tồn tại")
    return UserOut.from_user(row)


@router.post("/verify-email", response_model=UserOut)
def verify_email(data: VerifyEmailRequest, db: Session = Depends(get_db)):
    user = verify_email_with_token(db, data.token)
    if user is None:
        _auth_event("verify_email", "invalid")
        raise HTTPException(status_code=400, detail="Link xác thực không hợp lệ hoặc đã hết hạn")
    _auth_event("verify_email", "ok")
    return UserOut.from_user(user)


@router.post("/resend-verification")
async def resend_verification(
    request: Request,
    user: CurrentUser = Depends(get_current_user_allow_unverified),
    db: Session = Depends(get_db),
):
    ratelimit.hit("email:address", user.email)
    ratelimit.hit("email:ip", ratelimit.client_ip(request))
    if not is_email_configured():
        raise HTTPException(status_code=503, detail="Máy chủ chưa cấu hình gửi email")
    row = db.get(User, user.id)
    if row is None or row.email_verified_at is not None:
        return {"message": "Email đã được xác thực."}
    _queue_verification(row)
    return {"message": "Đã gửi lại email xác thực."}


@router.post("/forgot-password")
async def forgot_password(data: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    ip = ratelimit.client_ip(request)
    # Khoá theo cặp IP + email: khoá chỉ theo email cho phép bất kỳ ai gửi 5 yêu cầu để
    # chặn email đặt lại mật khẩu của người khác trong 1 giờ.
    ratelimit.hit("email:address", f"{ip}|{data.email}")
    ratelimit.hit("email:ip", ip)
    generic = {
        "message": "Nếu email tồn tại, một link đặt lại mật khẩu đã được gửi."
    }
    user = find_active_user_by_email(db, data.email)
    # Keep the response identical for unknown and disabled accounts, including
    # installations where email delivery is not configured.
    if not user or not is_email_configured():
        return generic
    token = create_reset_token(db, user)
    # Gửi nền: chờ SMTP chỉ với tài khoản có thật làm thời gian phản hồi lộ email nào tồn tại.
    _send_in_background(send_reset_email(
        user.email, f"{APP_PUBLIC_URL}/reset-password?token={token}"
    ))
    return generic


@router.post("/reset-password")
def reset_password(data: ResetPasswordRequest, db: Session = Depends(get_db)):
    if not reset_password_with_token(db, data.token, data.password):
        raise HTTPException(
            status_code=400,
            detail="Link đặt lại mật khẩu không hợp lệ hoặc đã hết hạn",
        )
    _auth_event("reset_password", "ok")
    return {"message": "Đặt lại mật khẩu thành công — hãy đăng nhập lại."}
