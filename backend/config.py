"""Cấu hình ứng dụng — Gemini, JWT, đường dẫn data."""

import math
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
# Sinh văn bản: "gemini" (mặc định) hoặc "ollama" (model chạy local, không cần key).
# OCR ảnh và embedding vẫn dùng Gemini.
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
# Per-provider I/O timeout, not a hard deadline for an entire streamed answer.
LLM_REQUEST_TIMEOUT_SEC: float = float(os.getenv("LLM_REQUEST_TIMEOUT_SEC", "60"))
# Trần token đầu ra (gồm cả token "thinking" của Gemini 2.5) cho mọi lời gọi sinh văn bản.
# Cùng với trần độ dài prompt, nó chặn trên chi phí thật của MỘT request — khoản đặt trước
# ngân sách (costs/budget.py) được tính từ chính con số này.
LLM_MAX_OUTPUT_TOKENS: int = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "4096"))
if LLM_MAX_OUTPUT_TOKENS < 256:
    raise ValueError("LLM_MAX_OUTPUT_TOKENS must be at least 256")
# Trần token đầu vào của một prompt sinh văn bản (prompt dài hơn bị từ chối trước khi gọi API).
LLM_MAX_INPUT_TOKENS: int = int(os.getenv("LLM_MAX_INPUT_TOKENS", "16000"))
if not math.isfinite(LLM_REQUEST_TIMEOUT_SEC) or LLM_REQUEST_TIMEOUT_SEC < 0.001:
    raise ValueError("LLM_REQUEST_TIMEOUT_SEC must be finite and at least 0.001")
# Tỉ lệ câu trả lời được LLM-as-judge chấm điểm (0–1). Mỗi lần chấm tốn thêm 1 lượt gọi Gemini.
JUDGE_SAMPLE_RATE: float = min(1.0, max(0.0, float(os.getenv("JUDGE_SAMPLE_RATE", "0.1"))))

# JWT
APP_ENV: str = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()
SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-in-production-32ch")
ALGORITHM: str = os.getenv("ALGORITHM", "HS256").strip()
# Khoá ký là bí mật đối xứng: chỉ các thuật toán HMAC hợp lệ ("none" hay RS*/ES* với khoá
# đối xứng đều là cấu hình sai, phải dừng ngay khi khởi động thay vì lộ ra lúc xác thực).
if ALGORITHM not in {"HS256", "HS384", "HS512"}:
    raise ValueError("ALGORITHM must be one of HS256, HS384, HS512")
ACCESS_TOKEN_EXPIRE_DAYS: int = 7
# Web console giữ phiên trong cookie httpOnly (JS không đọc được, XSS không lấy được token).
# Mobile/Public API vẫn dùng header Bearer.
AUTH_COOKIE_NAME: str = os.getenv("AUTH_COOKIE_NAME", "fieldops_session")
AUTH_COOKIE_SAMESITE: str = os.getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
if AUTH_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    raise ValueError("AUTH_COOKIE_SAMESITE must be lax, strict or none")
AUTH_COOKIE_DOMAIN: str | None = os.getenv("AUTH_COOKIE_DOMAIN") or None
# Header bắt buộc khi xác thực bằng cookie với phương thức ghi (chống CSRF): form/ảnh từ site
# khác không gửi được header tuỳ ý, còn fetch có header này phải qua CORS preflight.
CSRF_HEADER: str = "X-Requested-With"
CSRF_HEADER_VALUE: str = "fieldops"
RESET_TOKEN_EXPIRE_MINUTES: int = 30

# Google Sign-In: OAuth client ID loại "Web application" (có thể nhiều, cách nhau dấu phẩy
# — ví dụ thêm client iOS/Android cho app mobile). Để trống = tắt nút Google.
GOOGLE_CLIENT_IDS: list[str] = [item.strip() for item in os.getenv("GOOGLE_CLIENT_ID", "").split(",") if item.strip()]
# Bắt buộc xác thực email trước khi dùng tính năng (chỉ có hiệu lực khi đã cấu hình gửi email).
REQUIRE_EMAIL_VERIFICATION: bool = os.getenv("REQUIRE_EMAIL_VERIFICATION", "false").lower() in (
    "1",
    "true",
    "yes",
)
EMAIL_VERIFY_EXPIRE_HOURS: int = int(os.getenv("EMAIL_VERIFY_EXPIRE_HOURS", "48"))
# Chống dò mật khẩu: giới hạn số lần đăng nhập/đăng ký theo email và IP.
AUTH_RATE_LIMIT_ENABLED: bool = os.getenv("AUTH_RATE_LIMIT_ENABLED", "true").lower() not in (
    "0",
    "false",
    "no",
)

# Resend — gửi email quên mật khẩu
RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
RESEND_FROM_EMAIL: str = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")

# Chunking RAG (chunk lớn hơn → ít lần gọi embedding)
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "100"))
# Splitter chỉ báo lỗi lúc ingest tài liệu đầu tiên; kiểm tra ngay khi khởi động.
if CHUNK_SIZE < 1 or not 0 <= CHUNK_OVERLAP < CHUNK_SIZE:
    raise ValueError("CHUNK_SIZE must be positive and 0 <= CHUNK_OVERLAP < CHUNK_SIZE")
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
# Ảnh hiện trường (field/photos.py): ngoài thư mục web, chỉ tải qua URL ký có hạn.
FIELD_PHOTO_DIR: Path = DATA_ROOT / "field_photos"
FIELD_PHOTO_MAX_MB: int = int(os.getenv("FIELD_PHOTO_MAX_MB", "12"))
FIELD_PHOTO_MAX_PER_CASE: int = int(os.getenv("FIELD_PHOTO_MAX_PER_CASE", "40"))
FIELD_PHOTO_WORKSPACE_QUOTA_MB: int = int(os.getenv("FIELD_PHOTO_WORKSPACE_QUOTA_MB", "2048"))
FIELD_PHOTO_RETENTION_DAYS: int = int(os.getenv("FIELD_PHOTO_RETENTION_DAYS", "365"))
# M13 — địa chỉ → toạ độ qua Nominatim (OpenStreetMap). Chính sách Nominatim bắt buộc User-Agent có
# liên hệ: để trống NOMINATIM_CONTACT thì tắt tra cứu, chỉ nhập toạ độ tay (docs/design/M13…, Q4).
NOMINATIM_URL: str = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
NOMINATIM_CONTACT: str = os.getenv("NOMINATIM_CONTACT", "").strip()
GEOCODE_CACHE_DAYS: int = int(os.getenv("GEOCODE_CACHE_DAYS", "90"))

# M15 — thông báo đẩy qua Expo Push (miễn phí). Tắt: PUSH_ENABLED=0. EXPO_ACCESS_TOKEN không bắt buộc
# (bật "Enhanced security for push" trên expo.dev thì phải có). Giờ trong thông báo theo FIELD_TZ_OFFSET_MINUTES.
PUSH_ENABLED: bool = os.getenv("PUSH_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
EXPO_PUSH_URL: str = os.getenv("EXPO_PUSH_URL", "https://exp.host/--/api/v2/push/send")
EXPO_ACCESS_TOKEN: str = os.getenv("EXPO_ACCESS_TOKEN", "").strip()
FIELD_TZ_OFFSET_MINUTES: int = int(os.getenv("FIELD_TZ_OFFSET_MINUTES", "420"))

# Bước 7 (P1): phiên bản Chính sách quyền riêng tư (frontend/app/privacy + mobile). Đổi nội dung
# chính sách thì tăng phiên bản: người dùng cũ phải đồng ý lại khi mở app.
PRIVACY_POLICY_VERSION: str = "2026-11-23"
DATABASE_PATH: Path = DATA_ROOT / "app.db"
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{DATABASE_PATH}")
# Heroku/Render/Railway có thể đưa "postgres://…" — SQLAlchemy 2 không còn nhận tên đó và sập
# ngay lúc import. POSTGRES_URL bên dưới đã được chuẩn hoá cùng cách.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://") :]

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


DEV_ENVS = frozenset({"dev", "development", "local", "test"})


def is_production(app_env: str | None = None) -> bool:
    """MỘT định nghĩa cho mọi kiểm tra "production": mọi APP_ENV không phải môi trường dev.

    Trước đây khoá ký/metrics chỉ coi {prod, production, staging} là production, còn cookie/CORS
    coi mọi giá trị ngoài dev là production: APP_ENV=prd/live chấp nhận SECRET_KEY mặc định (ai
    cũng giả được JWT) và mở /metrics, trong khi cookie lại chạy như production.
    """
    return (APP_ENV if app_env is None else app_env).strip().lower() not in DEV_ENVS


def _public_url_problem(name: str, value: str) -> str | None:
    from urllib.parse import urlparse

    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        return f"{name} must be an https:// URL in production (got {value!r})"
    if parsed.hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return f"{name} must be the public URL, not {parsed.hostname}"
    return None


def validate_security_config() -> None:
    """Từ chối khởi động production khi cấu hình nguy hiểm hoặc thiếu.

    - SECRET_KEY riêng, ≥ 32 ký tự.
    - APP_PUBLIC_URL / API_PUBLIC_URL là https công khai: mặc định localhost làm link đặt lại mật
      khẩu, xác thực email, ảnh ký URL và URL webhook vào trỏ về localhost, widget nhúng bị 403.
    - CORS_ORIGINS không có "*", "null" hay http://: cookie phiên đi kèm credentials.
    """
    if not is_production():
        return
    if len(SECRET_KEY) < 32 or SECRET_KEY in {
        "dev-secret-change-in-production-32ch",
        "your_jwt_secret_32chars_minimum",
    }:
        raise RuntimeError("Production requires a unique SECRET_KEY of at least 32 characters")
    problems = [
        p
        for p in (
            _public_url_problem("APP_PUBLIC_URL", APP_PUBLIC_URL),
            _public_url_problem("API_PUBLIC_URL", API_PUBLIC_URL),
        )
        if p
    ]
    for origin in (o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()):
        if origin in {"*", "null"} or not origin.startswith("https://"):
            problems.append(f"CORS_ORIGINS must list exact https:// origins in production (got {origin!r})")
    if problems:
        raise RuntimeError("; ".join(problems))


# Văn bản trích ra tối đa bao nhiêu ký tự trên mỗi byte tệp gốc. DOCX/PDF nén (Flate) nên văn bản
# có thể dài hơn tệp; tài liệu thật hiếm khi quá ~3 ký tự/byte. rag/ingest.py từ chối tệp vượt mức
# và costs/budget.ingest_reserve_amount đặt trước theo đúng mức này, nên chi phí embedding không
# thể vượt phần đã giữ chỗ.
MAX_TEXT_CHARS_PER_BYTE = int(os.getenv("MAX_TEXT_CHARS_PER_BYTE", "5"))

# Số reverse proxy tin cậy đứng trước API (Render/Railway: edge của nền tảng; thêm 1 nếu đi qua
# proxy /api của Next). 0 = dùng IP kết nối trực tiếp (Caddy/nginx đã đặt qua --proxy-headers).
TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "0"))
