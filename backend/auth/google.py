"""Xác minh Google ID token (Google Identity Services) bằng PyJWT + JWKS công khai của Google.

Frontend lấy `credential` (ID token) từ nút "Đăng nhập với Google" và gửi lên
POST /auth/google. Backend tự kiểm chữ ký RS256, `aud` (client ID của ta), `iss`,
hạn dùng và `email_verified` — không tin bất kỳ thông tin nào frontend tự gửi.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import jwt

from config import GOOGLE_CLIENT_IDS

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleTokenError(ValueError):
    """ID token không hợp lệ; thông báo an toàn để trả về client."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool
    name: str | None = None
    picture: str | None = None


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    # PyJWKClient cache khoá theo `kid`; Google xoay khoá vài ngày một lần.
    return jwt.PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True, lifespan=3600, timeout=10)


def is_enabled() -> bool:
    return bool(GOOGLE_CLIENT_IDS)


def verify_id_token(credential: str, *, client_ids: list[str] | None = None) -> GoogleIdentity:
    audiences = client_ids if client_ids is not None else GOOGLE_CLIENT_IDS
    if not audiences:
        raise GoogleTokenError("Đăng nhập Google chưa được bật trên máy chủ")
    if not credential or len(credential) > 4096:
        raise GoogleTokenError("Thông tin đăng nhập Google không hợp lệ")
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(credential)
        claims = jwt.decode(
            credential,
            signing_key.key,
            algorithms=["RS256"],
            audience=audiences,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=30,
        )
    except jwt.PyJWTError as exc:
        raise GoogleTokenError("Thông tin đăng nhập Google không hợp lệ hoặc đã hết hạn") from exc
    return identity_from_claims(claims)


def identity_from_claims(claims: dict) -> GoogleIdentity:
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise GoogleTokenError("Token không do Google phát hành")
    sub = claims.get("sub")
    email = claims.get("email")
    if not isinstance(sub, str) or not sub or not isinstance(email, str) or "@" not in email:
        raise GoogleTokenError("Tài khoản Google thiếu email")
    verified = claims.get("email_verified")
    verified = verified is True or verified == "true"
    if not verified:
        raise GoogleTokenError("Email Google chưa được xác minh")
    return GoogleIdentity(
        sub=sub,
        email=email.strip().lower(),
        email_verified=True,
        name=claims.get("name"),
        picture=claims.get("picture"),
    )
