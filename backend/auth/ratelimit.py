"""Giới hạn tần suất cho các endpoint xác thực (chống dò mật khẩu, spam đăng ký/email).

Bộ đếm cửa sổ trượt trong bộ nhớ: đủ cho triển khai 1 instance (VPS free). Khi chạy
nhiều instance, chuyển sang Redis (REDIS_URL) để các worker dùng chung bộ đếm.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import HTTPException, Request

from config import AUTH_RATE_LIMIT_ENABLED, TRUSTED_PROXY_HOPS

# (số lần, cửa sổ giây)
LIMITS: dict[str, tuple[int, int]] = {
    # Sai mật khẩu đếm theo cặp (IP, email): đếm chỉ theo email thì người lạ gõ sai 10 lần là
    # khoá được tài khoản của bất kỳ ai. Trần theo riêng email cao hơn nhiều, chặn dò phân tán.
    "login:ip_email": (10, 15 * 60),
    "login:email": (50, 15 * 60),
    "login:ip": (30, 15 * 60),
    "register:ip": (10, 60 * 60),
    "google:ip": (30, 15 * 60),
    "email:address": (5, 60 * 60),
    "email:ip": (20, 60 * 60),
}

_lock = threading.Lock()
_hits: dict[tuple[str, str], deque[float]] = {}


def client_ip(request: Request | None) -> str:
    """IP của người dùng để đếm giới hạn.

    Mặc định dùng client.host: uvicorn chạy với --proxy-headers + --forwarded-allow-ips=<IP proxy>
    (Caddy/nginx) thì đó đã là IP thật. Trên Render/Railway, peer luôn là proxy của nền tảng nên
    mọi người dùng chung một IP — 30 lần đăng nhập sai ở bất kỳ đâu khoá đăng nhập của tất cả.
    Đặt TRUSTED_PROXY_HOPS=N (số proxy tin cậy phía trước API) để lấy phần tử thứ N TÍNH TỪ
    PHẢI của X-Forwarded-For: các proxy tin cậy tự nối thêm vào bên phải, nên client không giả
    được phần đó (giả ở bên trái thì bị bỏ qua). Không lấy phần tử trái nhất như --forwarded-allow-ips=*.
    """
    if request is None:
        return "unknown"
    hops = TRUSTED_PROXY_HOPS
    if hops > 0:
        chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(chain) >= hops:
            return chain[-hops]
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"


def hit(scope: str, key: str) -> None:
    """Đếm 1 lần; raise 429 nếu vượt giới hạn."""
    if not AUTH_RATE_LIMIT_ENABLED or not key:
        return
    limit, window = LIMITS[scope]
    now = time.monotonic()
    bucket_key = (scope, key.lower())
    with _lock:
        bucket = _hits.setdefault(bucket_key, deque())
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            retry = int(bucket[0] + window - now) + 1
            _record("rate_limited", scope)
            raise HTTPException(
                status_code=429,
                detail="Bạn thao tác quá nhanh. Vui lòng thử lại sau ít phút.",
                headers={"Retry-After": str(max(1, retry))},
            )
        bucket.append(now)
        if len(_hits) > 50_000:  # tránh phình bộ nhớ khi bị quét
            for stale in [k for k, v in _hits.items() if not v or v[-1] <= now - 3600]:
                _hits.pop(stale, None)


def reset(scope: str | None = None, key: str | None = None) -> None:
    with _lock:
        if scope is None:
            _hits.clear()
        else:
            _hits.pop((scope, (key or "").lower()), None)


def _record(event: str, result: str) -> None:
    try:
        from metrics import AUTH_EVENTS

        AUTH_EVENTS.labels(event=event, result=result).inc()
    except Exception:
        pass
