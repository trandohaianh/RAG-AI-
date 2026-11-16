"""Run the actual RAG chain and SSE routes against a real isolated database."""
import asyncio
import json

import pytest

import chat.router as chat
import embed.router as embed
import public_api.router as public_api
import rag.chain as chain
from chat.models import Message
import saas.limits as limits
from test_query_quota import quota_db, quota_client  # noqa: F401

REAL_ANSWER_RAG_ONCE = chain.answer_rag_once


def events(response):
    assert response.status_code == 200
    assert response.text.endswith("\n\n")
    return [json.loads(frame.removeprefix("data: ")) for frame in response.text.strip().split("\n\n")]


@pytest.mark.parametrize("channel", ["chat", "embed"])
@pytest.mark.parametrize("failure", ["provider", "commit"])
def test_failed_answer_is_not_done_or_persisted(quota_client, quota_db, monkeypatch, channel, failure):
    client, _, _ = quota_client
    async def retrieve(*args, **kwargs):
        return [("Evidence", "manual.pdf", 1)]
    def provider(prompt):
        yield "partial answer"
        if failure == "provider":
            raise RuntimeError("synthetic-secret-provider-url")
    monkeypatch.setattr(chain, "retrieve", retrieve)
    monkeypatch.setattr(chain, "_iter_tokens", provider)
    monkeypatch.setattr(chat, "stream_rag_answer", chain.stream_rag_answer)
    monkeypatch.setattr(embed, "stream_rag_answer", chain.stream_rag_answer)
    if failure == "commit":
        real_add = chat.chat_service.add_message
        def failed_add(db, conv_id, role, content, sources=None, **kwargs):
            if role == "assistant":
                db.add(Message(conversation_id=conv_id, role=role, content=content))
                db.flush()
                raise RuntimeError("synthetic-secret-storage-url")
            return real_add(db, conv_id, role, content, sources)
        monkeypatch.setattr(chat.chat_service, "add_message", failed_add)
    path, headers = ("/chat/1", {}) if channel == "chat" else ("/embed/chat", {"X-Embed-Token": "quota-embed-token"})
    response = client.post(path, json={"question": "question"}, headers=headers)
    frames = events(response)
    assert [frame["token"] for frame in frames if "token" in frame] == ["partial answer"]
    assert len([frame for frame in frames if "error" in frame]) == 1
    assert not any(frame.get("done") for frame in frames)
    assert "synthetic-secret" not in response.text
    with quota_db.sessions() as db:
        assert db.query(Message).filter_by(role="assistant").count() == 0
        assert db.query(Message).filter_by(role="user").count() == 1
        assert limits.daily_query_count(db, 1) == 1


@pytest.mark.parametrize("channel", ["chat", "embed", "api"])
def test_ollama_mode_is_not_blocked_by_missing_gemini_key(quota_client, monkeypatch, channel):
    client, calls, _ = quota_client
    module, path, headers = {
        "chat": (chat, "/chat/1", {}),
        "embed": (embed, "/embed/chat", {"X-Embed-Token": "quota-embed-token"}),
        "api": (public_api, "/v1/ask", {"X-API-Key": "quota-api-key"}),
    }[channel]
    monkeypatch.setattr(module, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(module, "GEMINI_API_KEY", "")
    assert client.post(path, json={"question": "question"}, headers=headers).status_code == 200
    assert len(calls) == 1


@pytest.mark.parametrize("channel", ["api", "field"])
def test_once_model_failure_returns_503_without_saving_a_recommendation(quota_client, quota_db, monkeypatch, channel):
    from field.models import ServiceCaseEvent
    client, _, _ = quota_client
    async def retrieve(*args, **kwargs):
        return [("Evidence", "manual.pdf", 1)]
    def fail(prompt):
        raise RuntimeError("synthetic-secret-provider-endpoint")
    monkeypatch.setattr(chain, "retrieve", retrieve)
    monkeypatch.setattr(chain, "generate_text", fail)
    monkeypatch.setattr(chain, "answer_rag_once", REAL_ANSWER_RAG_ONCE)
    monkeypatch.setattr(public_api, "answer_rag_once", REAL_ANSWER_RAG_ONCE)
    path, headers = ("/v1/ask", {"X-API-Key": "quota-api-key"}) if channel == "api" else ("/field/cases/1/assist", {})
    response = client.post(path, json={"question": "question"}, headers=headers)
    assert response.status_code == 503
    assert "synthetic-secret" not in response.text
    with quota_db.sessions() as db:
        assert db.query(Message).filter_by(role="assistant").count() == 0
        assert db.query(ServiceCaseEvent).filter_by(event_type="assistant_recommendation").count() == 0
        assert limits.daily_query_count(db, 1) == 1


def test_chat_disconnect_closes_nested_answer_generator(quota_db, monkeypatch):
    from auth.schemas import CurrentUser
    from chat.schemas import ChatRequest
    limits.initialize_query_usage(quota_db.engine)
    closed = []
    async def answer(*args, **kwargs):
        try:
            yield "token", "partial"
            await asyncio.Event().wait()
        finally:
            closed.append(True)
    monkeypatch.setattr(chat, "stream_rag_answer", answer)
    monkeypatch.setattr(chat, "LLM_PROVIDER", "ollama")
    async def run():
        with quota_db.sessions() as db:
            response = await chat.chat_stream(1, ChatRequest(question="question"), CurrentUser(id=1, email="owner@example.com", role="user"), db)
            iterator = response.body_iterator
            assert "partial" in await anext(iterator)
            await iterator.aclose()
    asyncio.run(run())
    assert closed == [True]
    with quota_db.sessions() as db:
        assert db.query(Message).filter_by(role="assistant").count() == 0
