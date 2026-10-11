"""SQLAlchemy engine, session và khởi tạo bảng."""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import DATABASE_PATH, DATABASE_URL

# Đảm bảo thư mục data tồn tại
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator:
    """Dependency inject session DB."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_sqlite_columns() -> None:
    """Thêm cột mới trên SQLite (create_all không alter bảng cũ)."""
    from sqlalchemy import inspect, text

    if not DATABASE_URL.startswith("sqlite"):
        return
    insp = inspect(engine)
    table_names = insp.get_table_names()
    if "users" in table_names:
        cols = {c["name"] for c in insp.get_columns("users")}
        with engine.begin() as conn:
            if "subscription_plan" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN subscription_plan VARCHAR(20) DEFAULT 'free'"
                    )
                )
            if "stripe_customer_id" not in cols:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN stripe_customer_id VARCHAR(255)")
                )

    if "messages" in table_names:
        msg_cols = {c["name"] for c in insp.get_columns("messages")}
        with engine.begin() as conn:
            if "rating" not in msg_cols:
                conn.execute(text("ALTER TABLE messages ADD COLUMN rating INTEGER"))
            if "eval_faithfulness" not in msg_cols:
                conn.execute(text("ALTER TABLE messages ADD COLUMN eval_faithfulness FLOAT"))
            if "eval_relevancy" not in msg_cols:
                conn.execute(text("ALTER TABLE messages ADD COLUMN eval_relevancy FLOAT"))


def init_db() -> None:
    """Tạo tất cả bảng SQLite."""
    from auth import models as auth_models  # noqa: F401
    from chat import models as chat_models  # noqa: F401
    from field import models as field_models  # noqa: F401
    from saas import models as saas_models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_columns()
