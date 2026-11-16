"""SQLAlchemy sessions and transactional, versioned application-schema upgrades."""
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import DATABASE_PATH, DATABASE_URL

DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _sqlite_concurrency(dbapi_connection, _connection_record) -> None:
    """API và tiến trình pipeline cùng ghi một file SQLite: WAL + chờ khoá thay vì lỗi ngay."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=not DATABASE_URL.startswith("sqlite"),
)
if engine.dialect.name == "sqlite":
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    if DATABASE_URL not in {"sqlite://", "sqlite:///:memory:"}:
        event.listen(engine, "connect", _sqlite_concurrency)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def utc_date(column, db=None):
    """Ngày UTC của một cột timestamptz, dùng cho GROUP BY theo ngày.

    Trên PostgreSQL, date(timestamptz) đổi theo múi giờ của session: nếu máy chủ DB không ở UTC,
    bucket theo ngày lệch khỏi cửa sổ UTC mà truy vấn dùng. SQLite lưu sẵn giá trị UTC.
    """
    from sqlalchemy import func

    bind = db.get_bind() if db is not None else engine
    if bind.dialect.name == "postgresql":
        return func.date(func.timezone("UTC", column))
    return func.date(column)


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _repair_legacy_accounts(conn, table_names: set[str]) -> None:
    """Compatibility entry point for historic migration callers."""
    from schema_migrations import repair_legacy_accounts

    repair_legacy_accounts(conn, table_names)


def _migrate_sqlite_columns() -> None:
    """Compatibility wrapper; new startup uses the shared versioned runner."""
    if engine.dialect.name == "sqlite":
        from schema_migrations import run_migrations

        run_migrations(engine)


def init_db() -> None:
    from auth import models as auth_models  # noqa: F401
    from chat import models as chat_models  # noqa: F401
    from costs import models as cost_models  # noqa: F401
    from pipeline.jobs import models as pipeline_models  # noqa: F401
    from field import models as field_models  # noqa: F401
    from saas import models as saas_models  # noqa: F401

    from schema_migrations import run_migrations

    run_migrations(engine, Base.metadata)
    from saas.limits import initialize_query_usage

    initialize_query_usage(engine)
