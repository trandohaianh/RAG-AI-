"""Header bảo mật cơ bản cho mọi phản hồi của API.

Caddy/nginx ở deploy/cloud và deploy/production đã thêm các header này ở biên, nhưng blueprint
Render/Railway và chạy uvicorn trực tiếp thì KHÔNG có reverse proxy: API trả JSON, widget.js và ảnh
hiện trường mà không có nosniff/frame policy. Middleware chỉ đặt header khi phản hồi chưa có, nên
không đè lên giá trị riêng của một route (vd. Content-Security-Policy của widget nếu có).
"""
from __future__ import annotations

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    # API không có trang nào cần nhúng iframe (widget /embed là trang của web, không phải API).
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)
HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


class SecurityHeadersMiddleware:
    def __init__(self, app, *, hsts: bool = False):
        self.app = app
        self.extra = SECURITY_HEADERS + ((HSTS,) if hsts else ())

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend((name, value) for name, value in self.extra if name not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
