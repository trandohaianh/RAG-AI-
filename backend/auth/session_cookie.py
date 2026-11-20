"""Cookie phiên httpOnly cho web console.

Web gửi `X-Auth-Mode: cookie` khi đăng nhập: server đặt JWT vào cookie httpOnly và KHÔNG trả
token trong body, nên JavaScript (kể cả mã XSS) không bao giờ đọc được token. Mobile và Public
API không gửi header này và tiếp tục nhận token để dùng header Bearer.
"""

from __future__ import annotations

from fastapi import Request, Response

import config

AUTH_MODE_HEADER = "x-auth-mode"


def wants_cookie(request: Request) -> bool:
    return request.headers.get(AUTH_MODE_HEADER, "").strip().lower() == "cookie"


def _secure() -> bool:
    # SameSite=None bắt buộc Secure; ngoài môi trường dev luôn chỉ gửi qua HTTPS.
    return config.is_production() or config.AUTH_COOKIE_SAMESITE == "none"


def set_session_cookie(response: Response, token: str, max_age: int | None = None) -> None:
    response.set_cookie(
        key=config.AUTH_COOKIE_NAME,
        value=token,
        max_age=max_age if max_age is not None else config.ACCESS_TOKEN_EXPIRE_DAYS * 24 * 3600,
        httponly=True,
        secure=_secure(),
        samesite=config.AUTH_COOKIE_SAMESITE,
        domain=config.AUTH_COOKIE_DOMAIN,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=config.AUTH_COOKIE_NAME,
        httponly=True,
        secure=_secure(),
        samesite=config.AUTH_COOKIE_SAMESITE,
        domain=config.AUTH_COOKIE_DOMAIN,
        path="/",
    )
