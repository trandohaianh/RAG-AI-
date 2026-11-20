"""Public widgets can resume only their own unpredictable guest session."""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import embed.router as embed
from auth.models import User
from auth.service import create_access_token
from chat.models import Conversation, Message
from database import get_db
from saas.models import EmbedGuestSession, EmbedToken
from saas.security import hash_secret
from test_auth_security import security_db  # noqa: F401


@pytest.fixture
def widget(security_db, monkeypatch):
    captured = []

    async def fake_answer(_user_id, _question, history, **kwargs):
        captured.append(history)
        yield "token", "Synthetic answer"
        yield "done", []

    monkeypatch.setattr(embed, "stream_rag_answer", fake_answer)
    monkeypatch.setattr(embed, "GEMINI_API_KEY", "test-only")
    monkeypatch.setattr(embed, "SessionLocal", security_db)
    monkeypatch.setattr(embed, "enforce_query_quota", lambda *_args: None)
    with security_db() as db:
        db.add_all(
            [
                EmbedToken(user_id=1, token_hash=hash_secret("widget-first-token")),
                EmbedToken(user_id=1, token_hash=hash_secret("widget-second-token")),
            ]
        )
        private = Conversation(user_id=1, title="Private chat")
        db.add(private)
        db.flush()
        db.add(Message(conversation_id=private.id, role="user", content="Private history"))
        db.commit()
        private_id = private.id
        owner_access = create_access_token(db.get(User, 1))

    app = FastAPI()
    app.include_router(embed.router)

    def db_override():
        with security_db() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as client:
        yield client, captured, private_id, owner_access


def ask(client, *, token="widget-first-token", **body):
    return client.post(
        "/embed/chat",
        json={"question": "What should I check?", **body},
        headers={"X-Embed-Token": token},
    )


def completed(response):
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    return next(event for event in events if event.get("done"))


def test_guest_session_is_private_and_resumable(widget, security_db):
    client, captured, _private_id, _owner_access = widget
    first = completed(ask(client))
    assert len(first["session_token"]) >= 32
    assert captured == [[]]
    resumed = completed(
        ask(
            client,
            conversation_id=first["conversation_id"],
            session_token=first["session_token"],
        )
    )
    assert resumed["conversation_id"] == first["conversation_id"]
    assert captured[-1][0]["role"] == "user"
    assert captured[-1][1]["content"] == "Synthetic answer"
    with security_db() as db:
        guest = db.query(EmbedGuestSession).one()
        assert guest.token_hash == hash_secret(first["session_token"])
        assert guest.token_hash != first["session_token"]
        assert (
            db.query(Message)
            .filter_by(
                conversation_id=first["conversation_id"],
                role="assistant",
            )
            .count()
            == 2
        )


def test_guessed_private_chat_and_guest_ids_cannot_be_resumed(widget, security_db):
    client, captured, private_id, _owner_access = widget
    first = completed(ask(client))
    second = completed(ask(client))
    for body in [
        {"conversation_id": private_id},
        {"conversation_id": private_id, "session_token": first["session_token"]},
        {"conversation_id": first["conversation_id"]},
        {"conversation_id": first["conversation_id"], "session_token": second["session_token"]},
        {"session_token": first["session_token"]},
    ]:
        assert ask(client, **body).status_code == 404
    assert (
        ask(
            client,
            token="widget-second-token",
            conversation_id=first["conversation_id"],
            session_token=first["session_token"],
        ).status_code
        == 404
    )
    assert len(captured) == 2
    with security_db() as db:
        assert db.query(Message).filter_by(conversation_id=private_id).count() == 1


def test_expired_session_and_revoked_token_are_rejected(widget, security_db):
    client, _captured, _private_id, owner_access = widget
    first = completed(ask(client))
    with security_db() as db:
        guest = db.query(EmbedGuestSession).one()
        guest.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        token_id = guest.embed_token_id
        db.commit()
    assert (
        ask(client, conversation_id=first["conversation_id"], session_token=first["session_token"]).status_code == 404
    )
    assert (
        client.delete(
            f"/embed/tokens/{token_id}",
            headers={
                "Authorization": f"Bearer {owner_access}",
            },
        ).status_code
        == 200
    )
    assert ask(client).status_code == 401
    with security_db() as db:
        assert db.query(EmbedGuestSession).count() == 0


def test_widget_rejects_disabled_owner_even_with_active_token(widget, security_db):
    client, _captured, _private_id, _owner_access = widget
    with security_db() as db:
        db.get(User, 1).deleted_at = datetime.now(UTC)
        db.commit()
    assert ask(client).status_code == 401
