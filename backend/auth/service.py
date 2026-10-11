"""Logic đăng ký, đăng nhập, JWT."""
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from auth.models import User
from auth.schemas import UserCreate
from config import (
    ACCESS_TOKEN_EXPIRE_DAYS,
    ALGORITHM,
    RESET_TOKEN_EXPIRE_MINUTES,
    SECRET_KEY,
)


def hash_password(password: str) -> str:
    """Băm mật khẩu bằng bcrypt (UTF-8)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {"sub": email, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def create_reset_token(email: str) -> str:
    """Token ngắn hạn (30 phút), có claim purpose='reset' để phân biệt với access token."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": email, "exp": expire, "purpose": "reset"}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_reset_token(token: str) -> str | None:
    """Trả về email nếu token hợp lệ và đúng mục đích reset, ngược lại None."""
    payload = decode_token(token)
    if not payload or payload.get("purpose") != "reset" or "sub" not in payload:
        return None
    return payload["sub"]


def set_password(db: Session, user: User, new_password: str) -> None:
    user.hashed_password = hash_password(new_password)
    db.commit()


def register_user(db: Session, data: UserCreate) -> User:
    """Đăng ký — user đầu tiên được gán role admin."""
    if db.query(User).filter(User.email == data.email).first():
        raise ValueError("Email đã được sử dụng")

    is_first = db.query(User).count() == 0
    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        role="admin" if is_first else "user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user
