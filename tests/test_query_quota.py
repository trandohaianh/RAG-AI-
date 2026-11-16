"""Durable quotas across channels, failure paths, upgrades, and real DB races."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import chat.router as chat
import embed.router as embed
import field.router as field
import public_api.router as public_api
import saas.limits as limits
from auth.models import User
from auth.schemas import CurrentUser
from chat.models import Conversation, Message
from database import Base, _enable_sqlite_foreign_keys, get_db
from dependencies import get_current_user
from field.models import FieldWorkspace, FieldWorkspaceMember, ServiceCase, ServiceCaseEvent
from saas.models import ApiKey, DailyQueryUsage, EmbedGuestSession, EmbedToken, QueryUsageMigration
from saas.security import hash_secret


@pytest.fixture
def quota_db(tmp_path):
    # Each racing worker gets a separate SQLite connection to a real database.
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'quota.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 20},
    )
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        db.add_all([
            User(id=1, email="owner@example.com", hashed_password="unused"),
            User(id=2, email="other@example.com", hashed_password="unused"),
        ])
        db.flush()
        db.add_all([
            Conversation(id=1, user_id=1, title="Own conversation"),
            Conversation(id=2, user_id=2, title="Private conversation"),
            FieldWorkspace(id=1, created_by=1, name="Field team"),
            ApiKey(user_id=1, name="Test", key_prefix="test", key_hash=hash_secret("quota-api-key")),
            EmbedToken(user_id=1, token_hash=hash_secret("quota-embed-token")),
        ])
        db.flush()
        db.add_all([
            FieldWorkspaceMember(workspace_id=1, user_id=1, role="owner"),
            ServiceCase(id=1, workspace_id=1, created_by=1, title="Disconnected", symptom="PON red"),
        ])
        db.commit()
    yield SimpleNamespace(engine=engine, sessions=sessions)
    engine.dispose()


@pytest.fixture
def quota_client(quota_db, monkeypatch):
    limits.initialize_query_usage(quota_db.engine)
    calls = []

    async def fake_stream(user_id, question, history, source=None, **kwargs):
        calls.append(("stream", user_id))
        yield "token", "Synthetic reply"
        yield "done", []

    async def fake_once(user_id, question, history=None, workspace_id=None, **kwargs):
        calls.append(("field" if workspace_id else "api", user_id))
        return "Synthetic reply", []

    for module in (chat, public_api, embed):
        monkeypatch.setattr(module, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(chat, "GEMINI_API_KEY", "test-only")
    monkeypatch.setattr(public_api, "GEMINI_API_KEY", "test-only")
    monkeypatch.setattr(embed, "GEMINI_API_KEY", "test-only")
    monkeypatch.setattr(chat, "stream_rag_answer", fake_stream)
    monkeypatch.setattr(embed, "stream_rag_answer", fake_stream)
    monkeypatch.setattr(public_api, "answer_rag_once", fake_once)
    monkeypatch.setattr("rag.chain.answer_rag_once", fake_once)
    monkeypatch.setattr(chat, "_schedule_eval", lambda *_args: None)
    monkeypatch.setattr(embed, "SessionLocal", quota_db.sessions)
    monkeypatch.setattr("database.SessionLocal", quota_db.sessions)
    monkeypatch.setattr(limits, "FREE_PLAN_DAILY_QUERIES", 4)
    actor = {"id": 1}
    app = FastAPI()
    for router in [chat.router, public_api.router, embed.router, field.router]:
        app.include_router(router)

    def db_override():
        with quota_db.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=actor["id"], email=f"user{actor['id']}@example.com", role="user"
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, calls, actor


def _usage(quota_db, user_id=1):
    with quota_db.sessions() as db:
        return limits.daily_query_count(db, user_id)


def _ask_all(client):
    return [
        client.post("/chat/1", json={"question": "Check?"}),
        client.post("/v1/ask", json={"question": "Check?"}, headers={"X-API-Key": "quota-api-key"}),
        client.post("/embed/chat", json={"question": "Check?"}, headers={"X-Embed-Token": "quota-embed-token"}),
        client.post("/field/cases/1/assist", json={"question": "Check?"}),
    ]


def test_all_paths_share_a_durable_quota_after_conversation_delete(quota_client, quota_db):
    client, calls, _actor = quota_client
    for response in _ask_all(client):
        assert response.status_code == 200, response.text
    assert _usage(quota_db) == 4
    assert len(calls) == 4
    assert client.delete("/conversations/1").status_code == 200
    assert _usage(quota_db) == 4
    for response in _ask_all(client)[1:]:
        assert response.status_code == 429, response.text
    assert client.post("/chat/2", json={"question": "Private?"}).status_code == 404
    assert len(calls) == 4


def test_permission_and_guest_session_rejections_do_not_charge(quota_client, quota_db):
    client, calls, actor = quota_client
    assert client.post("/chat/2", json={"question": "Private?"}).status_code == 404
    assert client.post("/embed/chat", json={
        "question": "Private?", "conversation_id": 2,
        "session_token": "unpredictable-but-invalid-session",
    }, headers={"X-Embed-Token": "quota-embed-token"}).status_code == 404
    assert client.post("/v1/ask", json={"question": "Private?"}, headers={
        "X-API-Key": "wrong-key",
    }).status_code == 401
    actor["id"] = 2
    assert client.post("/field/cases/1/assist", json={"question": "Private?"}).status_code in {403, 404}
    assert _usage(quota_db) == _usage(quota_db, 2) == 0
    assert calls == []


def test_closed_case_and_missing_model_config_do_not_charge(quota_client, quota_db, monkeypatch):
    client, calls, _actor = quota_client
    with quota_db.sessions() as db:
        db.get(ServiceCase, 1).status = "resolved"
        db.commit()
    assert client.post("/field/cases/1/assist", json={"question": "Check?"}).status_code == 409
    for module, path, headers in [
        (chat, "/chat/1", {}),
        (public_api, "/v1/ask", {"X-API-Key": "quota-api-key"}),
        (embed, "/embed/chat", {"X-Embed-Token": "quota-embed-token"}),
    ]:
        monkeypatch.setattr(module, "GEMINI_API_KEY", "")
        assert client.post(path, json={"question": "Check?"}, headers=headers).status_code == 500
    assert _usage(quota_db) == 0
    assert calls == []


def test_failed_generation_counts_even_without_a_saved_assistant(quota_client, quota_db, monkeypatch):
    client, calls, _actor = quota_client

    async def failed_answer(*_args, **_kwargs):
        raise RuntimeError("Synthetic provider failure")

    monkeypatch.setattr(public_api, "answer_rag_once", failed_answer)
    response = client.post("/v1/ask", json={"question": "Check?"}, headers={"X-API-Key": "quota-api-key"})
    assert response.status_code == 500
    assert _usage(quota_db) == 1
    with quota_db.sessions() as db:
        assert db.query(Message).filter(Message.role == "assistant").count() == 0
    assert calls == []


def test_local_preparation_failure_releases_the_reservation(quota_client, quota_db, monkeypatch):
    client, calls, _actor = quota_client

    def failed_save(*_args, **_kwargs):
        raise RuntimeError("Synthetic database preparation failure")

    monkeypatch.setattr(chat.chat_service, "add_message", failed_save)
    response = client.post("/chat/1", json={"question": "Check?"})
    assert response.status_code == 500
    assert _usage(quota_db) == 0
    assert calls == []


def test_quota_is_atomic_across_connections(quota_db, monkeypatch):
    monkeypatch.setattr(limits, "FREE_PLAN_DAILY_QUERIES", 7)
    barrier = Barrier(12)

    def reserve(_index):
        with quota_db.sessions() as db:
            barrier.wait(timeout=10)
            try:
                limits.enforce_query_quota(db, 1, "free")
                return 200
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=12) as workers:
        statuses = list(workers.map(reserve, range(12)))
    assert statuses.count(200) == 7
    assert statuses.count(429) == 5
    assert _usage(quota_db) == 7


def test_utc_rollover_and_plan_limits_are_independent_of_history(quota_db, monkeypatch):
    first_day = datetime(2026, 10, 21, tzinfo=timezone.utc).date()
    monkeypatch.setattr(limits, "_utc_today", lambda: first_day)
    monkeypatch.setattr(limits, "FREE_PLAN_DAILY_QUERIES", 1)
    monkeypatch.setattr(limits, "PRO_PLAN_DAILY_QUERIES", 2)
    with quota_db.sessions() as db:
        limits.enforce_query_quota(db, 1, "free")
        with pytest.raises(HTTPException):
            limits.enforce_query_quota(db, 1, "free")
        limits.enforce_query_quota(db, 1, "pro")
        assert limits.daily_query_count(db, 1) == 2
        monkeypatch.setattr(limits, "_utc_today", lambda: first_day + timedelta(days=1))
        assert limits.daily_query_count(db, 1) == 0
        limits.enforce_query_quota(db, 1, "free")
        assert limits.daily_query_count(db, 1) == 1
        assert db.query(DailyQueryUsage).filter_by(user_id=1).count() == 2


def test_legacy_backfill_is_one_time_and_includes_field_usage(quota_db, monkeypatch):
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with quota_db.sessions() as db:
        db.add_all([
            Message(conversation_id=1, role="assistant", content="Legacy reply", created_at=day_start),
            Message(conversation_id=1, role="user", content="Not usage", created_at=day_start),
            Message(conversation_id=1, role="assistant", content="Yesterday", created_at=day_start - timedelta(seconds=1)),
            ServiceCaseEvent(case_id=1, actor_id=1, event_type="assistant_recommendation", detail="Legacy field reply", created_at=day_start),
            ServiceCaseEvent(case_id=1, actor_id=1, event_type="field_note", detail="Not usage", created_at=day_start),
        ])
        db.commit()
    limits.initialize_query_usage(quota_db.engine)
    assert _usage(quota_db) == 2
    with quota_db.sessions() as db:
        limits.enforce_query_quota(db, 1, "pro")
        db.add(Message(conversation_id=1, role="assistant", content="Already counted", created_at=now))
        db.commit()
    limits.initialize_query_usage(quota_db.engine)
    assert _usage(quota_db) == 3
    with quota_db.sessions() as db:
        assert db.query(QueryUsageMigration).count() == 1
    monkeypatch.setattr(limits, "_utc_today", lambda: now.date() + timedelta(days=1))
    limits.initialize_query_usage(quota_db.engine)
    assert _usage(quota_db) == 0


def test_backfill_racing_workers_do_not_duplicate_usage(quota_db):
    with quota_db.sessions() as db:
        db.add(Message(conversation_id=1, role="assistant", content="One legacy reply"))
        db.commit()
    barrier = Barrier(4)

    def initialize(_index):
        barrier.wait(timeout=10)
        limits.initialize_query_usage(quota_db.engine)

    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(initialize, range(4)))
    assert _usage(quota_db) == 1
    with quota_db.sessions() as db:
        assert db.query(QueryUsageMigration).count() == 1


def test_exhausted_embed_quota_does_not_create_empty_guest_sessions(quota_client, quota_db):
    client, _calls, _actor = quota_client
    with quota_db.sessions() as db:
        for _ in range(4):
            limits.enforce_query_quota(db, 1, "free")
        conversation_count = db.query(Conversation).count()
        guest_count = db.query(EmbedGuestSession).count()
    for _ in range(3):
        response = client.post("/embed/chat", json={"question": "Check?"}, headers={
            "X-Embed-Token": "quota-embed-token",
        })
        assert response.status_code == 429, response.text
    with quota_db.sessions() as db:
        assert db.query(Conversation).count() == conversation_count
        assert db.query(EmbedGuestSession).count() == guest_count
    assert _usage(quota_db) == 4


def test_stream_and_field_failures_remain_counted(quota_client, quota_db, monkeypatch):
    client, _calls, _actor = quota_client

    async def failed_stream(*_args, **_kwargs):
        raise RuntimeError("Synthetic stream failure")
        yield

    async def failed_field(*_args, **_kwargs):
        raise RuntimeError("Synthetic field failure")

    monkeypatch.setattr(chat, "stream_rag_answer", failed_stream)
    monkeypatch.setattr("rag.chain.answer_rag_once", failed_field)
    response = client.post("/chat/1", json={"question": "Check?"})
    assert response.status_code == 200
    assert "Synthetic stream failure" not in response.text
    assert "rag_unavailable" in response.text
    assert client.post("/field/cases/1/assist", json={"question": "Check?"}).status_code == 500
    assert _usage(quota_db) == 2


def test_expired_widget_guest_does_not_use_another_query(quota_client, quota_db):
    client, calls, _actor = quota_client
    headers = {"X-Embed-Token": "quota-embed-token"}
    first = client.post("/embed/chat", json={"question": "Check?"}, headers=headers)
    assert first.status_code == 200
    completed = next(
        json.loads(line[6:]) for line in first.text.splitlines()
        if line.startswith("data: ") and json.loads(line[6:]).get("done")
    )
    with quota_db.sessions() as db:
        guest = db.query(EmbedGuestSession).one()
        guest.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    rejected = client.post("/embed/chat", json={
        "question": "Check again?", "conversation_id": completed["conversation_id"],
        "session_token": completed["session_token"],
    }, headers=headers)
    assert rejected.status_code == 404
    assert _usage(quota_db) == 1
    assert len(calls) == 1
