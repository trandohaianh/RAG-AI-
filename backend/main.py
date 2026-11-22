"""
FastAPI — RAG Chatbot production (Gemini + JWT + multi-user).
"""

import asyncio
import hmac
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from admin.router import router as admin_router
from analytics.router import router as analytics_router
from auth.router import router as auth_router
from auth.schemas import CurrentUser
from billing.router import router as billing_router
from chat.router import router as chat_router
from config import APP_ENV, CHROMA_DIR, UPLOAD_DIR, validate_security_config
from cors import cors_options
from costs.router import refresh_infra_gauge
from costs.router import router as costs_router
from database import init_db
from dependencies import require_admin
from docs.router import router as docs_router
from embed.router import router as embed_router
from field.closures import router as field_closures_router
from field.dispatch import router as field_dispatch_router
from field.geocode import router as field_geocode_router
from field.integrations import public_router as field_integrations_public_router
from field.integrations import router as field_integrations_router
from field.photos import router as field_photos_router
from field.push import router as field_push_router
from field.router import router as field_router
from metrics import metrics_response
from middleware_metrics import PrometheusMiddleware
from pipeline.db import init_medallion_schema
from pipeline.jobs.router import router as pipeline_router
from public_api.router import router as public_api_router
from rag.cache import close_cache
from rag.document_lifecycle import recover_all_pending
from security_headers import SecurityHeadersMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_security_config()
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    recovered = await asyncio.to_thread(recover_all_pending, CHROMA_DIR, upload_root=UPLOAD_DIR)
    if recovered:
        logging.getLogger(__name__).info("Recovered %d interrupted document publications", recovered)
    try:
        init_medallion_schema()
    except Exception as exc:
        # The warehouse is optional for local SQLite-only runs, but swallowing
        # every exception makes a broken production connection look healthy.
        logging.getLogger(__name__).warning(
            "Medallion schema initialization unavailable (%s); continuing without warehouse",
            type(exc).__name__,
        )
    try:
        refresh_infra_gauge()
    except Exception:
        logging.getLogger(__name__).debug("Infra cost gauge not initialised")
    try:
        yield
    finally:
        await asyncio.to_thread(close_cache)


def _api_docs_urls() -> dict:
    """Swagger/ReDoc/OpenAPI chỉ bật ngoài production (chủ sản phẩm chọn 2026-11-17).

    nginx (deploy/production) vốn đã chặn chúng; Caddy cloud, Render, Railway thì không, nên bản
    đồ toàn bộ endpoint bị công khai. Đọc tài liệu API khi chạy local (APP_ENV=development).
    """
    from config import is_production

    if is_production():
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {}


app = FastAPI(
    title="Field Service Intelligence API",
    description="Field-service workspaces, case workflows, and grounded RAG assistance.",
    version="5.0.0",
    lifespan=lifespan,
    **_api_docs_urls(),
)


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception):
    """Lỗi không lường trước: ghi log đầy đủ, trả JSON {detail} như mọi lỗi khác.

    Mặc định Starlette trả text "Internal Server Error" — frontend đọc `detail` nên hiện thông báo
    rỗng/khó hiểu. Không đưa chi tiết lỗi ra ngoài (đường dẫn, câu SQL, lỗi provider).
    """
    from fastapi.responses import JSONResponse

    logging.getLogger("fieldops.errors").exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Lỗi máy chủ. Vui lòng thử lại sau."}, status_code=500)


app.add_middleware(PrometheusMiddleware)
app.add_middleware(CORSMiddleware, **cors_options(APP_ENV, os.environ))
# Thêm sau cùng = lớp ngoài cùng: cả phản hồi lỗi CORS/preflight cũng mang header bảo mật.
# HSTS chỉ ở production (HTTPS); dev chạy http://127.0.0.1 sẽ bị trình duyệt nhớ nhầm.
app.add_middleware(SecurityHeadersMiddleware, hsts=APP_ENV == "production")

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(docs_router)
app.include_router(field_router)
app.include_router(field_photos_router)
app.include_router(field_closures_router)
app.include_router(field_geocode_router)
app.include_router(field_push_router)
app.include_router(field_dispatch_router)
app.include_router(field_integrations_router)
app.include_router(field_integrations_public_router)
app.include_router(admin_router)
app.include_router(analytics_router)
app.include_router(embed_router)
app.include_router(billing_router)
app.include_router(public_api_router)
app.include_router(costs_router)
app.include_router(pipeline_router)


@app.get("/health")
def health():
    return {"status": "ok"}


def _metrics_authorized(request: Request) -> bool:
    """Bảo vệ /metrics khi deploy công khai.

    Đặt METRICS_TOKEN rồi cấu hình Grafana Cloud Metrics Endpoint dùng Bearer token
    (hoặc Basic với user bất kỳ + password = token). Không đặt → mở (chạy local).
    """
    expected = os.getenv("METRICS_TOKEN", "").strip()
    if not expected:
        from config import is_production

        return not is_production()
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer":
        return hmac.compare_digest(value.strip(), expected)
    if scheme.lower() == "basic":
        import base64

        try:
            decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
        except Exception:
            return False
        _, _, password = decoded.partition(":")
        return hmac.compare_digest(password, expected)
    return False


@app.get("/metrics")
def prometheus_metrics(request: Request):
    if not _metrics_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Bearer"})
    body, content_type = metrics_response()
    return Response(content=body, media_type=content_type)


def _llm_readiness(provider: str, gemini_key: str) -> str:
    """Ollama: phải gọi được thật. Trước đây chỉ cần LLM_PROVIDER=ollama là báo "ok", kể cả khi
    stack production không có service ollama nào (OLLAMA_URL trỏ vào tên DNS không tồn tại)."""
    if provider != "ollama":
        return "ok" if gemini_key else "missing_key"
    import httpx

    from config import OLLAMA_URL

    try:
        httpx.get(f"{OLLAMA_URL.rstrip('/')}/api/tags", timeout=2.0).raise_for_status()
        return "ok"
    except Exception as exc:
        return f"unreachable:{type(exc).__name__}"


@app.get("/health/ready")
def readiness():
    """Kiểm tra sâu cho uptime monitor: DB đọc được, thư mục dữ liệu ghi được, cấu hình AI."""
    from sqlalchemy import text as sql_text

    from config import GEMINI_API_KEY, LLM_PROVIDER
    from database import engine

    checks: dict[str, str] = {}
    try:
        with engine.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error:{type(exc).__name__}"
    try:
        probe = CHROMA_DIR / f".ready-{secrets.token_hex(4)}"
        probe.write_text("ok")
        probe.unlink()
        checks["storage"] = "ok"
    except Exception as exc:
        checks["storage"] = f"error:{type(exc).__name__}"
    checks["llm"] = _llm_readiness(LLM_PROVIDER, GEMINI_API_KEY)

    # Tính năng tuỳ chọn: "disabled" là hợp lệ; chỉ lỗi khi ĐÃ cấu hình mà không dùng được.
    from auth.email_utils import email_channel
    from config import COHERE_API_KEY, GOOGLE_CLIENT_IDS, REDIS_URL
    from pipeline.db import is_medallion_enabled

    features = {
        "google_login": "enabled" if GOOGLE_CLIENT_IDS else "disabled",
        "email": email_channel() or "disabled",
        "cohere_rerank": "enabled" if COHERE_API_KEY else "disabled",
        "redis_cache": "enabled" if REDIS_URL else "disabled",
        "warehouse": "disabled",
    }
    if is_medallion_enabled():
        try:
            from pipeline.db import get_engine

            with get_engine().connect() as conn:
                conn.execute(sql_text("SELECT 1"))
            features["warehouse"] = "ok"
        except Exception as exc:
            features["warehouse"] = f"error:{type(exc).__name__}"
            checks["warehouse"] = features["warehouse"]
    healthy = all(v == "ok" for v in checks.values())
    if not healthy:
        raise HTTPException(status_code=503, detail={"status": "degraded", "checks": checks, "features": features})
    return {"status": "ok", "checks": checks, "features": features}


@app.get("/de/gold/stats")
def de_gold_stats(_admin: CurrentUser = Depends(require_admin)):
    """Gold layer snapshot — cho demo DE / Grafana."""
    from sqlalchemy import text

    from pipeline.db import is_medallion_enabled, session_scope

    if not is_medallion_enabled():
        return {"enabled": False, "message": "Bật POSTGRES_URL + MEDALLION_ENABLED"}

    with session_scope() as db:
        daily = (
            db.execute(text("SELECT * FROM gold.daily_ingest_stats ORDER BY metric_date DESC LIMIT 7")).mappings().all()
        )
        summaries = (
            db.execute(
                text(
                    "SELECT user_id, source, chunk_count, total_chars, last_ingested_at "
                    "FROM gold.document_summary ORDER BY last_ingested_at DESC LIMIT 20"
                )
            )
            .mappings()
            .all()
        )

    def _row(row):
        d = dict(row)
        for k, v in d.items():
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
        return d

    return {
        "enabled": True,
        "daily_ingest_stats": [_row(r) for r in daily],
        "document_summaries": [_row(r) for r in summaries],
    }
