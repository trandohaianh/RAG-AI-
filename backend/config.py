"""Cấu hình ứng dụng — Gemini, JWT, đường dẫn data."""
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent

# backend/.env trước; d:\self\.env ghi đè (tránh placeholder trong backend/.env)
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(ROOT_DIR / ".env", override=True)

# Gemini
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")
LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-2.5-flash")

# JWT
SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-in-production-32ch")
ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_DAYS: int = 7
RESET_TOKEN_EXPIRE_MINUTES: int = 30

# Resend — gửi email quên mật khẩu
RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
RESEND_FROM_EMAIL: str = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")

# Chunking RAG (chunk lớn hơn → ít lần gọi embedding)
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "100"))
TOP_K: int = int(os.getenv("TOP_K", "5"))
# Lấy nhiều chunk hơn trước rerank (tuần 1–2)
RETRIEVE_CANDIDATES: int = int(os.getenv("RETRIEVE_CANDIDATES", "15"))

# Redis — cache kết quả retrieve (tuần 1–2)
REDIS_URL: str = os.getenv("REDIS_URL", "")
REDIS_CACHE_TTL_SEC: int = int(os.getenv("REDIS_CACHE_TTL_SEC", "300"))

# Cohere Rerank (tuần 1–2) — https://dashboard.cohere.com/api-keys
COHERE_API_KEY: str = os.getenv("COHERE_API_KEY", "")
COHERE_RERANK_MODEL: str = os.getenv("COHERE_RERANK_MODEL", "rerank-multilingual-v3.0")

# Gemini free tier: ~100 embed requests/phút — batch nhỏ + chờ giữa các batch
EMBED_BATCH_SIZE: int = int(os.getenv("EMBED_BATCH_SIZE", "20"))
EMBED_BATCH_DELAY_SEC: float = float(os.getenv("EMBED_BATCH_DELAY_SEC", "1.5"))
EMBED_MAX_RETRIES: int = int(os.getenv("EMBED_MAX_RETRIES", "5"))

# Đường dẫn (Docker: DATA_DIR=/app/data)
DATA_ROOT = Path(os.getenv("DATA_DIR", str(ROOT_DIR / "data")))
CHROMA_DIR: Path = DATA_ROOT / "chroma_db"
UPLOAD_DIR: Path = DATA_ROOT / "uploads"
DATABASE_PATH: Path = DATA_ROOT / "app.db"
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{DATABASE_PATH}")

# PostgreSQL Medallion (DE warehouse)
_postgres = os.getenv(
    "POSTGRES_URL",
    "postgresql+psycopg2://rag:rag@localhost:5432/rag_dw",
)
# Render/Railway thường trả postgres:// — chuẩn hóa cho SQLAlchemy
if _postgres.startswith("postgres://"):
    _postgres = _postgres.replace("postgres://", "postgresql+psycopg2://", 1)
POSTGRES_URL: str = _postgres
MEDALLION_ENABLED: bool = os.getenv("MEDALLION_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".png", ".jpg", ".jpeg", ".webp", ".gif"}

# Tuần 5–8: SaaS
APP_PUBLIC_URL: str = os.getenv("APP_PUBLIC_URL", "http://localhost:3000")
API_PUBLIC_URL: str = os.getenv("API_PUBLIC_URL", "http://localhost:8000")
FREE_PLAN_DAILY_QUERIES: int = int(os.getenv("FREE_PLAN_DAILY_QUERIES", "50"))
PRO_PLAN_DAILY_QUERIES: int = int(os.getenv("PRO_PLAN_DAILY_QUERIES", "1000"))

# Stripe (tuần 7–8)
STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_PRO: str = os.getenv("STRIPE_PRICE_PRO", "")
