"""Exercise quota locking and migration with isolated PostgreSQL schemas."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timezone
from threading import Barrier

from fastapi import HTTPException
from sqlalchemy.orm import Session

import saas.limits as limits
from auth.models import User
from chat.models import Conversation, Message
from database import Base
from saas.models import DailyQueryUsage, QueryUsageMigration
from schema_migrations import run_migrations
from test_postgres_migrations import postgres_engine  # noqa: F401


def _seed(engine, *, reply_day=None):
    run_migrations(engine, Base.metadata)
    with Session(engine) as db:
        db.add(User(id=1, email="quota-pg@example.com", hashed_password="unused"))
        db.flush()
        db.add(Conversation(id=1, user_id=1, title="PostgreSQL quota test"))
        db.flush()
        if reply_day is not None:
            db.add_all(
                [
                    Message(
                        conversation_id=1,
                        role="assistant",
                        content="retained",
                        created_at=datetime.combine(reply_day, datetime.min.time(), tzinfo=UTC),
                    )
                    for _ in range(3)
                ]
            )
        db.commit()


def test_postgres_quota_is_atomic_across_real_connections(postgres_engine, monkeypatch):
    _seed(postgres_engine)
    limits.initialize_query_usage(postgres_engine)
    monkeypatch.setattr(limits, "FREE_PLAN_DAILY_QUERIES", 7)
    barrier = Barrier(12)

    def reserve(_index):
        with Session(postgres_engine) as db:
            barrier.wait(timeout=20)
            try:
                limits.enforce_query_quota(db, 1, "free")
                return 200
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=12) as workers:
        statuses = list(workers.map(reserve, range(12)))
    assert statuses.count(200) == 7
    assert statuses.count(429) == 5
    with Session(postgres_engine) as db:
        assert limits.daily_query_count(db, 1) == 7


def test_postgres_usage_baseline_runs_once_across_concurrent_workers(postgres_engine, monkeypatch):
    day = date(2026, 10, 21)
    monkeypatch.setattr(limits, "_utc_today", lambda: day)
    _seed(postgres_engine, reply_day=day)
    barrier = Barrier(6)

    def initialize(_index):
        barrier.wait(timeout=20)
        limits.initialize_query_usage(postgres_engine)

    with ThreadPoolExecutor(max_workers=6) as workers:
        list(workers.map(initialize, range(6)))
    with Session(postgres_engine) as db:
        assert limits.daily_query_count(db, 1) == 3
        assert db.query(QueryUsageMigration).count() == 1
        db.query(Message).delete()
        db.commit()
    limits.initialize_query_usage(postgres_engine)
    with Session(postgres_engine) as db:
        assert limits.daily_query_count(db, 1) == 3


def test_postgres_release_and_history_delete_preserve_other_utc_day(postgres_engine, monkeypatch):
    day = date(2026, 10, 21)
    monkeypatch.setattr(limits, "_utc_today", lambda: day)
    _seed(postgres_engine)
    limits.initialize_query_usage(postgres_engine)
    with Session(postgres_engine) as db:
        previous = limits.enforce_query_quota(db, 1, "free")
    day = date(2026, 10, 22)
    with Session(postgres_engine) as db:
        # A delayed answer from yesterday is persisted today. Restart must not
        # seed a second charge from retained history on the new UTC day.
        db.add(
            Message(
                conversation_id=1,
                role="assistant",
                content="delayed reply",
                created_at=datetime(2026, 10, 22, tzinfo=UTC),
            )
        )
        db.commit()
    limits.initialize_query_usage(postgres_engine)
    with Session(postgres_engine) as db:
        assert limits.daily_query_count(db, 1) == 0
        limits.enforce_query_quota(db, 1, "free")
        limits.release_query_quota(db, previous)
        assert limits.daily_query_count(db, 1) == 1
        assert db.get(DailyQueryUsage, (1, previous.usage_date)).used_queries == 0
        db.delete(db.get(Conversation, 1))
        db.commit()
        assert limits.daily_query_count(db, 1) == 1
