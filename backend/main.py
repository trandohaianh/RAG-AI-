"""
FastAPI — RAG Chatbot production (Gemini + JWT + multi-user).
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from admin.router import router as admin_router
from analytics.router import router as analytics_router
from auth.router import router as auth_router
from billing.router import router as billing_router
from chat.router import router as chat_router
from embed.router import router as embed_router
from public_api.router import router as public_api_router
import os

from config import CHROMA_DIR, UPLOAD_DIR
from database import init_db
from docs.router import router as docs_router
from metrics import metrics_response
from middleware_metrics import PrometheusMiddleware
from pipeline.db import init_medallion_schema


@asynccontextmanager
async def lifespan(_app: FastAPI):
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    try:
        init_medallion_schema()
    except Exception:
        pass  # Postgres có thể chưa chạy (dev chỉ SQLite)
    yield


app = FastAPI(
    title="RAG Chatbot API",
    description="Production RAG với Gemini, JWT, per-user ChromaDB",
    version="4.0.0",
    lifespan=lifespan,
)

app.add_middleware(PrometheusMiddleware)
_cors_default = ",".join(
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1")
    for port in range(3000, 3010)
)
_cors_origins = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", _cors_default).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(docs_router)
app.include_router(admin_router)
app.include_router(analytics_router)
app.include_router(embed_router)
app.include_router(billing_router)
app.include_router(public_api_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def prometheus_metrics():
    body, content_type = metrics_response()
    return Response(content=body, media_type=content_type)


@app.get("/de/gold/stats")
def de_gold_stats():
    """Gold layer snapshot — cho demo DE / Grafana."""
    from pipeline.db import is_medallion_enabled, session_scope
    from sqlalchemy import text

    if not is_medallion_enabled():
        return {"enabled": False, "message": "Bật POSTGRES_URL + MEDALLION_ENABLED"}

    with session_scope() as db:
        daily = db.execute(
            text("SELECT * FROM gold.daily_ingest_stats ORDER BY metric_date DESC LIMIT 7")
        ).mappings().all()
        summaries = db.execute(
            text(
                "SELECT user_id, source, chunk_count, total_chars, last_ingested_at "
                "FROM gold.document_summary ORDER BY last_ingested_at DESC LIMIT 20"
            )
        ).mappings().all()
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
