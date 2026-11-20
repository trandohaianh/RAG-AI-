"""Durable daily RAG quotas, shared by every paid/public application path.

A reservation counts an accepted processing attempt even if generation or the
client connection later fails. Only failures before entering RAG release it.
SQLite and PostgreSQL serialize the conditional counter update in the database;
there is no process-local lock or dependency on retained chat messages.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, literal, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from chat.models import Conversation, Message
from config import FREE_PLAN_DAILY_QUERIES, PRO_PLAN_DAILY_QUERIES
from saas.models import DailyQueryUsage, QueryUsageMigration


@dataclass(frozen=True)
class QueryReservation:
    user_id: int
    usage_date: date


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _insert(db: Session, table):
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise RuntimeError(f"Query quota does not support database dialect: {dialect}")
    return insert(table)


def initialize_query_usage(engine: Engine) -> None:
    """Snapshot retained legacy usage exactly once, before serving requests.

    The marker and counters commit together. Concurrent application workers race
    on a unique marker, so only one can backfill; a crash rolls both back. Later
    UTC days start at zero, preventing a stream that crosses midnight from being
    charged again by a history-based seed during restart.
    """
    from auth.models import User
    from field.models import ServiceCaseEvent

    day = _utc_today()
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    with Session(engine) as db:
        claimed = db.execute(
            _insert(db, QueryUsageMigration)
            .values(name="durable-query-usage-v1")
            .on_conflict_do_nothing(index_elements=["name"])
        )
        if claimed.rowcount != 1:
            db.rollback()
            return
        messages = (
            select(func.count(Message.id))
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.user_id == User.id,
                Message.role == "assistant",
                Message.created_at >= start,
                Message.created_at < end,
            )
            .correlate(User)
            .scalar_subquery()
        )
        field_replies = (
            select(func.count(ServiceCaseEvent.id))
            .where(
                ServiceCaseEvent.actor_id == User.id,
                ServiceCaseEvent.event_type == "assistant_recommendation",
                ServiceCaseEvent.created_at >= start,
                ServiceCaseEvent.created_at < end,
            )
            .correlate(User)
            .scalar_subquery()
        )
        # WHERE also disambiguates SQLite INSERT .. SELECT .. ON CONFLICT.
        baseline = select(User.id, literal(day), messages + field_replies).where(User.id > 0)
        db.execute(
            _insert(db, DailyQueryUsage)
            .from_select(["user_id", "usage_date", "used_queries"], baseline)
            .on_conflict_do_nothing(index_elements=["user_id", "usage_date"])
        )
        db.commit()


def _ensure_day(db: Session, user_id: int, day: date) -> None:
    db.execute(
        _insert(db, DailyQueryUsage)
        .values(user_id=user_id, usage_date=day, used_queries=0)
        .on_conflict_do_nothing(index_elements=["user_id", "usage_date"])
    )


def daily_query_count(db: Session, user_id: int) -> int:
    value = db.scalar(
        select(DailyQueryUsage.used_queries).where(
            DailyQueryUsage.user_id == user_id,
            DailyQueryUsage.usage_date == _utc_today(),
        )
    )
    return value or 0


def plan_daily_limit(plan: str) -> int:
    return PRO_PLAN_DAILY_QUERIES if plan == "pro" else FREE_PLAN_DAILY_QUERIES


def enforce_query_quota(db: Session, user_id: int, plan: str) -> QueryReservation:
    """Atomically reserve and commit one attempt before entering RAG.

    Call only after permission and input checks. This function owns a short
    transaction; callers must not have pending business writes to commit.
    """
    day = _utc_today()
    limit = plan_daily_limit(plan)
    try:
        _ensure_day(db, user_id, day)
        result = db.execute(
            update(DailyQueryUsage)
            .where(
                DailyQueryUsage.user_id == user_id,
                DailyQueryUsage.usage_date == day,
                DailyQueryUsage.used_queries < limit,
            )
            .values(used_queries=DailyQueryUsage.used_queries + 1)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(
                status_code=429,
                detail=f"\u0110\u00e3 \u0111\u1ea1t gi\u1edbi h\u1ea1n {limit} c\u00e2u h\u1ecfi/ng\u00e0y (g\u00f3i {plan}). N\u00e2ng c\u1ea5p Pro \u0111\u1ec3 t\u0103ng h\u1ea1n m\u1ee9c.",
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return QueryReservation(user_id=user_id, usage_date=day)


def release_query_quota(db: Session, reservation: QueryReservation) -> None:
    """Undo only a local preparation failure before RAG has been entered."""
    db.rollback()
    db.execute(
        update(DailyQueryUsage)
        .where(
            DailyQueryUsage.user_id == reservation.user_id,
            DailyQueryUsage.usage_date == reservation.usage_date,
            DailyQueryUsage.used_queries > 0,
        )
        .values(used_queries=DailyQueryUsage.used_queries - 1)
        .execution_options(synchronize_session=False)
    )
    db.commit()
