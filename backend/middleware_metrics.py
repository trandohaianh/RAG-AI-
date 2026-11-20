"""Middleware ghi metric HTTP theo route template (không theo URL thô).

Dùng `/chat/{conv_id}` thay vì `/chat/123` để số series không tăng theo dữ liệu.
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from metrics import HTTP_DURATION, HTTP_REQUESTS


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)
        start = time.perf_counter()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            route = _route_template(request)
            HTTP_REQUESTS.labels(method=request.method, endpoint=route, status=status).inc()
            HTTP_DURATION.labels(method=request.method, route=route).observe(time.perf_counter() - start)
