"""Usage reporting follows durable counters rather than retained chat history."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import admin.router as admin
from analytics.service import daily_series, user_overview
from auth.models import User
from auth.schemas import CurrentUser
from chat.models import Conversation, Message
from database import Base
from saas.limits import initialize_query_usage
from saas.models import DailyQueryUsage


@pytest.fixture
def reporting_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with sessions() as db:
        db.add_all([
            User(id=1, email="active@example.com", hashed_password="unused", role="admin"),
            User(id=2, email="other@example.com", hashed_password="unused"),
        ])
        db.flush()
        conversation = Conversation(user_id=1, title="Reporting")
        db.add(conversation)
        db.flush()
        conversation_id = conversation.id
        db.add_all([
            Message(conversation_id=conversation_id, role="assistant", content="legacy", created_at=today - timedelta(hours=1)),
            Message(conversation_id=conversation_id, role="assistant", content="today", created_at=now),
        ])
        db.commit()
    initialize_query_usage(engine)
    with sessions() as db:
        db.get(DailyQueryUsage, (1, today.date())).used_queries = 4
        db.get(DailyQueryUsage, (2, today.date())).used_queries = 20
        db.get(User, 2).deleted_at = now
        db.commit()
        yield db, conversation_id, today
    engine.dispose()


def test_analytics_uses_counter_for_recorded_days_and_retains_legacy_history(reporting_db):
    db, _conversation_id, today = reporting_db
    series = daily_series(db, 1, days=2)
    assert series == [
        {"date": (today - timedelta(days=1)).date().isoformat(), "queries": 1},
        {"date": today.date().isoformat(), "queries": 4},
    ]
    assert user_overview(db, 1, "free")["queries_today"] == 4


def test_admin_and_user_usage_survive_conversation_deletion(reporting_db, monkeypatch):
    db, conversation_id, today = reporting_db

    async def no_documents(_user_id):
        return []

    monkeypatch.setattr(admin, "list_user_docs", no_documents)
    actor = CurrentUser(id=1, email="active@example.com", role="admin")
    before = asyncio.run(admin.get_stats(actor, db))
    assert before.queries_today == 4
    db.delete(db.get(Conversation, conversation_id))
    db.commit()
    after = asyncio.run(admin.get_stats(actor, db))
    assert after.total_messages == 0
    assert after.queries_today == 4
    assert daily_series(db, 1, days=1)[0]["queries"] == 4
    assert user_overview(db, 1, "free")["queries_today"] == 4
