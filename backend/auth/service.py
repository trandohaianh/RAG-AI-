"""Authentication with typed JWTs and transactional credential revocation."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from jwt import PyJWTError
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth.models import PasswordResetToken, User
from auth.schemas import UserCreate
from config import (
    ACCESS_TOKEN_EXPIRE_DAYS,
    ALGORITHM,
    RESET_TOKEN_EXPIRE_MINUTES,
    SECRET_KEY,
)


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > 72:
        raise ValueError("Mật khẩu tối đa 72 byte UTF-8")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        raw = plain.encode("utf-8")
        if len(raw) > 72:
            return False
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user: User, expires_at: datetime | None = None) -> str:
    """`expires_at`: giữ nguyên hạn của token gốc khi chỉ đổi hình thức phiên (bearer -> cookie)."""
    expire = expires_at or datetime.now(UTC) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user.id),
        "exp": expire,
        "purpose": "access",
        "version": user.token_version,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except PyJWTError:
        return None


def _typed_claims(token: str, purpose: str) -> dict | None:
    payload = decode_token(token)
    if (
        not payload
        or payload.get("purpose") != purpose
        or type(payload.get("version")) is not int
        or payload["version"] < 0
        or not isinstance(payload.get("sub"), str)
        or not payload["sub"].isascii()
        or not payload["sub"].isdigit()
        or len(payload["sub"]) > 19
        or not 0 < int(payload["sub"]) <= 9223372036854775807
    ):
        return None
    return payload


def decode_access_token(token: str) -> dict | None:
    return _typed_claims(token, "access")


def create_reset_token(db: Session, user: User) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)
    token_id = secrets.token_urlsafe(32)
    payload = {
        "sub": str(user.id),
        "exp": expire,
        "purpose": "reset",
        "version": user.token_version,
        "jti": token_id,
    }
    db.add(
        PasswordResetToken(
            token_hash=hashlib.sha256(token_id.encode("utf-8")).hexdigest(),
            user_id=user.id,
            token_version=user.token_version,
            expires_at=expire,
        )
    )
    db.commit()
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_reset_token(token: str) -> dict | None:
    payload = _typed_claims(token, "reset")
    if not payload or not isinstance(payload.get("jti"), str) or not payload["jti"]:
        return None
    return payload


def _change_password(db: Session, user_id: int, version: int, new_password: str) -> bool:
    changed = db.execute(
        update(User)
        .where(
            User.id == user_id,
            User.deleted_at.is_(None),
            User.token_version == version,
        )
        .values(
            hashed_password=hash_password(new_password),
            token_version=User.token_version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    return changed.rowcount == 1


def reset_password_with_token(db: Session, token: str, new_password: str) -> bool:
    payload = decode_reset_token(token)
    if not payload:
        return False
    now = datetime.now(UTC)
    token_hash = hashlib.sha256(payload["jti"].encode("utf-8")).hexdigest()
    try:
        # This conditional UPDATE serializes concurrent consumers. The claim and
        # password/version change commit together, or both roll back.
        claimed = db.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.user_id == int(payload["sub"]),
                PasswordResetToken.token_version == payload["version"],
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
            .values(used_at=now)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1 or not _change_password(db, int(payload["sub"]), payload["version"], new_password):
            db.rollback()
            return False
        db.commit()
        db.expire_all()
        return True
    except Exception:
        db.rollback()
        raise


def set_password(db: Session, user: User, new_password: str) -> None:
    if not _change_password(db, user.id, user.token_version, new_password):
        db.rollback()
        raise ValueError("Thông tin xác thực đã thay đổi; hãy thử lại")
    db.commit()
    db.refresh(user)


def register_user(db: Session, data: UserCreate) -> User:
    from sqlalchemy import func as sa_func

    # Hộp thư không phân biệt hoa/thường: "Alice@x.com" và "alice@x.com" là cùng một người.
    # Không chặn ở đây thì có thể có hai tài khoản, và đăng nhập Google (so sánh lower(email))
    # sẽ liên kết vào một trong hai một cách tuỳ ý.
    if db.query(User).filter(sa_func.lower(User.email) == data.email.lower()).first():
        raise ValueError("Email đã được sử dụng")
    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        role="user",
    )
    db.add(user)
    try:
        db.flush()
        # The first allocated identity is the bootstrap administrator. This
        # avoids granting admin to two concurrent registrations on an empty DB.
        if user.id == 1:
            user.role = "admin"
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("Email đã được sử dụng") from exc
    db.refresh(user)
    return user


def find_active_user_by_email(db: Session, email: str) -> User | None:
    """Tìm tài khoản đang hoạt động theo email, không phân biệt hoa/thường.

    Ưu tiên khớp chính xác (dữ liệu cũ có thể còn hai bản khác hoa/thường); nếu không có
    thì chỉ nhận khi khớp không phân biệt hoa/thường đúng MỘT tài khoản, để không chọn bừa.
    """
    from sqlalchemy import func as sa_func

    email = (email or "").strip()
    exact = db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first()
    if exact is not None:
        return exact
    matches = (
        db.query(User).filter(sa_func.lower(User.email) == email.lower(), User.deleted_at.is_(None)).limit(2).all()
    )
    return matches[0] if len(matches) == 1 else None


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = find_active_user_by_email(db, email)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


# ------------------------------------------------------------------ email verification
def create_email_verification_token(user: User) -> str:
    from config import EMAIL_VERIFY_EXPIRE_HOURS

    expire = datetime.now(UTC) + timedelta(hours=EMAIL_VERIFY_EXPIRE_HOURS)
    payload = {
        "sub": str(user.id),
        "exp": expire,
        "purpose": "verify_email",
        "version": user.token_version,
        # Gắn email để link cũ mất hiệu lực nếu email tài khoản thay đổi.
        "email": hashlib.sha256(user.email.lower().encode("utf-8")).hexdigest(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_email_with_token(db: Session, token: str) -> User | None:
    payload = _typed_claims(token, "verify_email")
    if not payload or not isinstance(payload.get("email"), str):
        return None
    user = db.query(User).filter(User.id == int(payload["sub"]), User.deleted_at.is_(None)).first()
    if user is None:
        return None
    if not hmac_compare(payload["email"], hashlib.sha256(user.email.lower().encode("utf-8")).hexdigest()):
        return None
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
        db.commit()
        db.refresh(user)
    return user


def hmac_compare(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ------------------------------------------------------------------ Google Sign-In
_UNUSABLE_PASSWORD = "!external-login"


def login_with_google(db: Session, identity) -> tuple[User, bool]:
    """Đăng nhập/đăng ký bằng Google. Trả về (user, created).

    - Đã liên kết google_sub → đăng nhập.
    - Chưa liên kết nhưng email trùng tài khoản mật khẩu → liên kết (Google đã xác minh email).
    - Chưa có → tạo tài khoản mới (không có mật khẩu; có thể đặt sau qua "Quên mật khẩu").
    """
    from sqlalchemy import func as sa_func

    now = datetime.now(UTC)
    user = db.query(User).filter(User.google_sub == identity.sub).first()
    if user is not None:
        if user.deleted_at is not None:
            raise ValueError("Tài khoản đã bị vô hiệu hoá")
        if user.email_verified_at is None:
            user.email_verified_at = now
            db.commit()
        return user, False

    user = db.query(User).filter(sa_func.lower(User.email) == identity.email.lower()).first()
    if user is not None:
        if user.deleted_at is not None:
            raise ValueError("Tài khoản đã bị vô hiệu hoá")
        if user.google_sub and user.google_sub != identity.sub:
            raise ValueError("Email này đã liên kết với tài khoản Google khác")
        if user.email_verified_at is None:
            # Chống pre-hijack: ai cũng đăng ký được bằng email của người khác mà không cần
            # xác minh. Google vừa chứng minh chủ hộp thư là người đang đăng nhập, nên mật khẩu
            # và mọi phiên do người đăng ký trước tạo ra không được giữ quyền vào tài khoản này.
            # Chủ thật có thể đặt mật khẩu mới qua "Quên mật khẩu".
            user.hashed_password = _UNUSABLE_PASSWORD
            user.token_version = (user.token_version or 0) + 1
            user.email_verified_at = now
        user.google_sub = identity.sub
        db.commit()
        db.refresh(user)
        return user, False

    user = User(
        email=identity.email,
        hashed_password=_UNUSABLE_PASSWORD,
        role="user",
        auth_provider="google",
        google_sub=identity.sub,
        email_verified_at=now,
    )
    db.add(user)
    try:
        db.flush()
        if user.id == 1:
            user.role = "admin"
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("Không thể tạo tài khoản, vui lòng thử lại") from exc
    db.refresh(user)
    return user, True
