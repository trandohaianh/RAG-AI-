"""Regression tests for round 5 (2026-11-17, multi-role review): algorithm and QA findings.
Each test fails on commit ddf8352."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import public_api.router as public_api
import rag.chain as chain
import saas.limits as limits
from auth.models import User
from database import Base, _enable_sqlite_foreign_keys
from evals.graders import fact_coverage, forbidden_hits, groundedness
from field.models import FieldWorkspace, ServiceCase
from pipeline.jobs import models as pipeline_models  # noqa: F401
from pipeline.jobs.tasks import compute_day
from rag.bm25 import tokenize
from rag.grounding import sentence_grounding_score
from rag.rerank import _tokens as rerank_tokens
from rag.technical.store import lexical_tokens
from saas import models as saas_models  # noqa: F401
from test_query_quota import quota_client, quota_db  # noqa: F401

REAL_ANSWER_RAG_ONCE = chain.answer_rag_once


def nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


# ---------------------------------------------------------------- NFD Vietnamese text (macOS / Unikey tổ hợp / PDF)
def test_bm25_tokens_are_the_same_for_composed_and_decomposed_vietnamese():
    assert tokenize(nfd("Lỗi mất nguồn E-204")) == tokenize("Lỗi mất nguồn E-204")


def test_lexical_rerank_tokens_ignore_unicode_normal_form():
    assert rerank_tokens(nfd("Kiểm tra đèn LOS")) == rerank_tokens("Kiểm tra đèn LOS")


def test_technical_fts_tokens_ignore_unicode_normal_form():
    assert lexical_tokens(nfd("Điện áp nguồn")) == lexical_tokens("Điện áp nguồn")


def test_identical_sentence_in_nfd_is_grounded():
    result = sentence_grounding_score(nfd("Kiểm tra đèn LOS trên thiết bị."), "Kiểm tra đèn LOS trên thiết bị.")
    assert result["grounded"] == 1


# ---------------------------------------------------------------- eval graders: negation scope
def test_prohibited_action_is_not_counted_as_the_required_fact():
    coverage, missing = fact_coverage("Không được phép tắt nguồn ONU.", [["tắt nguồn"]])
    assert coverage == 0.0 and missing == [["tắt nguồn"]]
    # an affirmative mention still counts
    assert fact_coverage("Kiểm tra đèn, tắt nguồn ONU rồi bật lại.", [["tắt nguồn"]])[0] == 1.0


def test_safety_warning_is_not_a_forbidden_instruction():
    assert forbidden_hits("Không được phép rút dây quang khi đang đo.", ["rút dây quang"]) == []
    assert forbidden_hits("Rút dây quang ra khi đang đo.", ["rút dây quang"]) == ["rút dây quang"]


def test_groundedness_rejects_an_answer_that_negates_its_source():
    source = ["Tắt nguồn ONU khi đang cập nhật firmware."]
    assert groundedness("Không tắt nguồn ONU khi đang cập nhật firmware.", source) == 0.0
    assert groundedness("Tắt nguồn ONU khi đang cập nhật firmware.", source) == 1.0


# ---------------------------------------------------------------- KPI: resolved cases stay on the day they were resolved
def test_resolved_case_kpi_does_not_move_to_the_day_of_a_later_edit():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine, autoflush=False)
    day_d = datetime.now(UTC).date() - timedelta(days=1)
    with make() as db:
        db.add(User(id=1, email="o@example.com", hashed_password="x"))
        db.add(FieldWorkspace(id=1, name="Đội", created_by=1))
        case = ServiceCase(workspace_id=1, created_by=1, title="Mất mạng", symptom="LOS đỏ")
        db.add(case)
        db.commit()
        case.status = "resolved"
        db.commit()
        # Pretend it was resolved yesterday; today a dispatcher edits it again.
        resolved = datetime.combine(day_d, datetime.min.time(), tzinfo=UTC) + timedelta(hours=10)
        case.resolved_at = resolved
        case.updated_at = resolved
        db.commit()
        case.priority = "high"
        db.commit()
        assert compute_day(db, day_d).cases_resolved == 1
        assert compute_day(db, day_d + timedelta(days=1)).cases_resolved == 0
        # reopening clears the timestamp
        case.status = "in_progress"
        db.commit()
        assert case.resolved_at is None
    engine.dispose()


# ---------------------------------------------------------------- QA: retrieval outage is a clean 503, not a bare 500
@pytest.mark.parametrize("channel", ["api", "field"])
def test_retrieval_failure_returns_public_503(quota_client, quota_db, monkeypatch, channel):
    client, _, _ = quota_client

    async def retrieve(*args, **kwargs):
        raise RuntimeError("synthetic-secret-chroma-path")

    monkeypatch.setattr(chain, "retrieve", retrieve)
    monkeypatch.setattr(chain, "answer_rag_once", REAL_ANSWER_RAG_ONCE)
    monkeypatch.setattr(public_api, "answer_rag_once", REAL_ANSWER_RAG_ONCE)
    path, headers = ("/v1/ask", {"X-API-Key": "quota-api-key"}) if channel == "api" else ("/field/cases/1/assist", {})
    response = client.post(path, json={"question": "question"}, headers=headers)
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert "synthetic-secret" not in response.text
    with quota_db.sessions() as db:
        assert limits.daily_query_count(db, 1) == 1


# ---------------------------------------------------------------- security: DOCX decompression bomb
def test_docx_zip_bomb_is_rejected_before_extraction(tmp_path, monkeypatch):
    import zipfile

    from rag import ingest

    bomb = tmp_path / "bomb.docx"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"<w:t>a</w:t>" * (1024 * 1024))  # ~12 MB -> ~12 KB
    opened = []
    monkeypatch.setattr(ingest, "Docx2txtLoader", lambda path: opened.append(path))
    with pytest.raises(ingest.UnsafeDocumentError):
        ingest._load_sync(bomb)
    assert opened == []


def test_normal_docx_still_loads(tmp_path, monkeypatch):
    import zipfile

    from rag import ingest

    doc = tmp_path / "ok.docx"
    with zipfile.ZipFile(doc, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<w:t>Quy trình đo công suất quang ONT</w:t>")
    from langchain_core.documents import Document

    class Loader:
        def __init__(self, path):
            self.path = path

        def load(self):
            return [Document(page_content="Quy trình đo công suất quang ONT", metadata={"source": self.path})]

    monkeypatch.setattr(ingest, "Docx2txtLoader", Loader)
    assert ingest._load_sync(doc)


# ---------------------------------------------------------------- security: OCR output is capped like every other call
def test_ocr_call_caps_output_tokens(monkeypatch):
    from types import SimpleNamespace

    import config
    from rag import llm

    seen = {}

    def generate_content(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(text="chữ", usage_metadata=None)

    monkeypatch.setattr(
        llm, "client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    monkeypatch.setattr(llm, "record_usage", lambda **kw: None)
    llm.read_image_text(b"\x89PNG", "image/png", "đọc chữ")
    assert seen["config"].max_output_tokens == config.LLM_MAX_OUTPUT_TOKENS


# ---------------------------------------------------------------- security: GET that spends AI budget needs the CSRF header
def test_suggested_questions_requires_csrf_header_for_cookie_sessions():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import config
    from docs.router import router as docs_router

    app = FastAPI()
    app.include_router(docs_router)
    with TestClient(app) as client:
        client.cookies.set(config.AUTH_COOKIE_NAME, "any-token")
        response = client.get("/documents/suggested-questions")
    assert response.status_code == 403
