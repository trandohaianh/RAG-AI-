"""Regression tests for round 4 (2026-11-17): close the remaining gaps listed in
reports/01_Technical_Code_Audit_Report.md §4. Each test fails on commit 845aec1."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import chat.router as chat_router
import config
from costs import budget, models as cost_models  # noqa: F401
from costs.pricing import compute_cost
from database import Base, _enable_sqlite_foreign_keys
from rag import llm, prompt


@pytest.fixture
def sessions():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False)
    engine.dispose()


# ---------------------------------------------------------------- one request cannot outspend its reservation
def test_generation_output_is_capped_so_cost_stays_within_the_reservation(monkeypatch):
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text="ok", usage_metadata=None)

    monkeypatch.setattr(llm, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm, "client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    monkeypatch.setattr(llm, "record_usage", lambda **kw: None)
    llm.generate_text("câu hỏi")                         # caller did not pass max_tokens
    llm.generate_text("câu hỏi", max_tokens=10 ** 6)     # caller asked for more than the cap
    assert all(c["config"].max_output_tokens == config.LLM_MAX_OUTPUT_TOKENS for c in calls)


def test_streaming_generation_is_capped_too(monkeypatch):
    seen = {}

    def stream(**kwargs):
        seen.update(kwargs)
        return iter([])

    monkeypatch.setattr(llm, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm, "client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content_stream=stream)))
    monkeypatch.setattr(llm, "record_usage", lambda **kw: None)
    with pytest.raises(RuntimeError):
        list(llm.stream_text(prompt.build_gemini_prompt("ctx", "q")))
    assert seen["config"].max_output_tokens == config.LLM_MAX_OUTPUT_TOKENS
    assert llm._ollama_body("p", True, False, None, llm._bounded("p", None))["options"]["num_predict"] \
        == config.LLM_MAX_OUTPUT_TOKENS


def test_oversized_prompt_is_refused_before_calling_the_provider(monkeypatch):
    monkeypatch.setattr(llm, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm, "client", lambda: pytest.fail("provider must not be called"))
    with pytest.raises(ValueError):
        llm.generate_text("x" * (config.LLM_MAX_INPUT_TOKENS * 4 + 400))


def test_default_reservation_covers_the_largest_possible_request(monkeypatch):
    monkeypatch.delenv("LLM_BUDGET_RESERVE_USD", raising=False)
    worst = compute_cost(config.LLM_MODEL, config.LLM_MAX_INPUT_TOKENS, config.LLM_MAX_OUTPUT_TOKENS)
    assert budget.reserve_amount() >= worst
    monkeypatch.setattr(config, "COHERE_API_KEY", "set")
    assert budget.reserve_amount() >= worst + compute_cost(config.COHERE_RERANK_MODEL, units=1)


def test_large_upload_reserves_by_document_size(sessions, monkeypatch):
    monkeypatch.setenv("LLM_USER_DAILY_BUDGET_USD", "0.03")
    big = 400 * 1024 * 1024                          # bytes — embedding cost alone exceeds the cap
    assert budget.ingest_reserve_amount(big) > budget.reserve_amount()
    with sessions() as db:
        with pytest.raises(HTTPException):
            budget.enforce_budget(db, 5, budget.ingest_reserve_amount(big))
        budget.enforce_budget(db, 5).release()          # a normal question still fits


# ---------------------------------------------------------------- background judge respects the budget
def test_background_judge_is_skipped_when_the_budget_is_exhausted(monkeypatch, sessions):
    judged = []

    async def fake_judge(question, context, answer):
        judged.append(question)
        return None

    monkeypatch.setattr(chat_router, "judge_answer", fake_judge)
    monkeypatch.setattr(chat_router, "JUDGE_SAMPLE_RATE", 1.0)
    monkeypatch.setattr("database.SessionLocal", sessions)
    monkeypatch.setenv("LLM_USER_DAILY_BUDGET_USD", "0.001")
    monkeypatch.setenv("LLM_BUDGET_RESERVE_USD", "0.01")

    async def run():
        chat_router._schedule_eval(1, "q", "ctx", "a", user_id=9)
        await asyncio.gather(*list(chat_router._background_tasks))

    asyncio.run(run())
    assert judged == [], "judge calls must not bypass the daily AI budget"
