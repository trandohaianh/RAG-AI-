"""Kết nối PostgreSQL — Medallion warehouse (tách khỏi SQLite app)."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config import MEDALLION_ENABLED, POSTGRES_URL

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def is_medallion_enabled() -> bool:
    return MEDALLION_ENABLED and bool(POSTGRES_URL)


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        if not POSTGRES_URL:
            raise RuntimeError("POSTGRES_URL chưa cấu hình")
        _engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    return _engine


def get_session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_medallion_schema() -> None:
    """Chạy init.sql nếu bảng chưa có (local dev không qua docker entrypoint)."""
    if not is_medallion_enabled():
        return
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sql_file = root / "infra" / "postgres" / "init.sql"
    if not sql_file.exists():
        return
    sql = sql_file.read_text(encoding="utf-8")
    engine = get_engine()
    with engine.begin() as conn:
        for stmt in sql.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))
