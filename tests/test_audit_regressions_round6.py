"""Regression tests for round 6 (2026-11-17): everything left open by the multi-role review,
plus the owner decisions (technical RAG top-N + decimal comma, same-origin /api, bounded session
renewal + logout-all, chars/2.5 token estimate, RRF-preserving rerank, webhook retry policy,
dev/holdout eval split). Each test fails on commit b767f92."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import socket
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import rag.chain as chain
from auth.models import User
from auth.service import decode_access_token, hash_password
from costs import models as cost_models
from field import integrations
from field import models as field_models
from test_auth_providers import client, db_factory  # noqa: F401
from test_query_quota import quota_client, quota_db  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


# ============================================================================ budget off the event loop
def test_budget_reservation_never_runs_on_the_event_loop(quota_client, monkeypatch):  # noqa: F811
    import costs.budget as budget

    client_, _, _ = quota_client
    seen = []
    real = budget.enforce_budget

    def spy(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            seen.append("event-loop")
        except RuntimeError:
            seen.append("worker-thread")
        return real(*args, **kwargs)

    monkeypatch.setattr(budget, "enforce_budget", spy)
    monkeypatch.setenv("LLM_USER_DAILY_BUDGET_USD", "5")
    assert client_.post("/chat/1", json={"question": "Đèn LOS đỏ thì kiểm tra gì?"}).status_code == 200
    assert client_.post("/field/cases/1/assist", json={"question": "Đèn LOS đỏ thì kiểm tra gì?"}).status_code == 200
    assert (
        client_.post(
            "/v1/ask", json={"question": "Đèn LOS đỏ thì kiểm tra gì?"}, headers={"X-API-Key": "quota-api-key"}
        ).status_code
        == 200
    )
    assert seen and set(seen) == {"worker-thread"}, seen


def test_reservation_is_returned_when_the_client_leaves_before_the_first_chunk(quota_client, quota_db, monkeypatch):  # noqa: F811
    import anyio

    client_, _, _ = quota_client
    monkeypatch.setenv("LLM_USER_DAILY_BUDGET_USD", "5")
    body = json.dumps({"question": "q"}).encode()
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            await anyio.sleep_forever()  # client never reads: generator is never iterated

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/chat/1",
        "raw_path": b"/chat/1",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"host", b"test")],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }

    async def run():
        with anyio.fail_after(10):
            await client_.app(scope, receive, send)

    anyio.run(run)
    with quota_db.sessions() as db:
        assert db.query(cost_models.LlmBudgetReservation).count() == 0


# ============================================================================ webhooks
def _trickling_server(stop: threading.Event) -> int:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\n")
        while not stop.is_set():  # one header byte at a time, never finishing
            try:
                conn.sendall(b"X")
            except OSError:
                break
            time.sleep(0.2)
        conn.close()
        server.close()

    threading.Thread(target=serve, daemon=True).start()
    return server.getsockname()[1]


def test_slow_loris_receiver_cannot_hold_the_dispatcher(monkeypatch):
    monkeypatch.setattr(integrations, "WEBHOOK_DEADLINE_SECONDS", 1.0)
    stop = threading.Event()
    port = _trickling_server(stop)
    started = time.monotonic()
    try:
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            integrations._DeadlineClient().post(f"http://127.0.0.1:{port}/hook", b"{}", {})
    finally:
        stop.set()
    assert time.monotonic() - started < 5


def test_first_retry_is_about_thirty_seconds_with_jitter():
    delays = {integrations._backoff(1).total_seconds() for _ in range(50)}
    assert all(24 <= d <= 36 for d in delays) and len(delays) > 1
    assert 48 <= integrations._backoff(2).total_seconds() <= 72


@pytest.mark.parametrize("code,dead", [(400, True), (404, True), (410, True), (408, False), (429, False), (503, False)])
def test_permanent_client_errors_go_straight_to_dead_letter(code, dead):
    assert integrations._permanent_failure(code) is dead


# ============================================================================ default team race
def test_concurrent_first_launch_creates_one_default_team(quota_db):  # noqa: F811
    from auth.schemas import CurrentUser
    from field.router import list_workspaces

    with quota_db.sessions() as db:
        db.add(User(id=9, email="new@example.com", hashed_password="x"))
        db.commit()
    user = CurrentUser(id=9, email="new@example.com", role="user")
    # Request A already read "no team yet"; request B created the team meanwhile.
    with quota_db.sessions() as other:
        list_workspaces(user, other)
    with quota_db.sessions() as db:
        real_scalars = db.scalars
        calls = []

        def stale_first_read(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return SimpleNamespace(all=lambda: [])
            return real_scalars(*args, **kwargs)

        db.scalars = stale_first_read
        list_workspaces(user, db)
    with quota_db.sessions() as db:
        assert db.query(field_models.FieldWorkspaceMember).filter_by(user_id=9).count() == 1


# ============================================================================ streaming
def test_disconnected_client_returns_its_stream_slot_while_the_provider_read_continues(monkeypatch):
    async def retrieve(*args, **kwargs):
        return [("Reviewed evidence", "manual.pdf", 1)]

    release = threading.Event()

    def provider(prompt):
        yield "first"
        release.wait(5)
        yield "late"

    monkeypatch.setattr(chain, "retrieve", retrieve)
    monkeypatch.setattr(chain, "_iter_tokens", provider)
    monkeypatch.setattr(chain, "_STREAM_SLOTS", threading.BoundedSemaphore(1))

    async def run():
        first = chain.stream_rag_answer(1, "q")
        try:
            await anext(first)
            await anext(first)
            await asyncio.wait_for(first.aclose(), timeout=0.5)
            second = chain.stream_rag_answer(1, "again")
            await anext(second)  # context
            assert (await anext(second))[0] == "token", "one dropped client must not block the next"
            await second.aclose()
        finally:
            release.set()

    asyncio.run(run())


def test_citation_audit_work_is_linear_in_the_answer_length(monkeypatch):
    async def retrieve(*args, **kwargs):
        return [("Evidence text", "manual.pdf", 1)]

    tokens = ["chữ "] * 400
    audited = []
    real_audit = chain.audit_citations

    def counting_audit(text, bundle):
        audited.append(len(text))
        return real_audit(text, bundle)

    monkeypatch.setattr(chain, "retrieve", retrieve)
    monkeypatch.setattr(chain, "_iter_tokens", lambda prompt: iter(tokens))
    monkeypatch.setattr(chain, "audit_citations", counting_audit)

    async def run():
        return [kind async for kind, _ in chain.stream_rag_answer(1, "q", include_evidence=True)]

    asyncio.run(run())
    answer_length = sum(map(len, tokens))
    assert sum(audited) <= 3 * answer_length, f"audited {sum(audited)} chars for a {answer_length}-char answer"


# ============================================================================ KPIs / quality
def test_refusals_are_not_sent_to_the_judge(monkeypatch):
    import chat.router as chat_router
    from rag.chain import _NO_DOC_MSG

    monkeypatch.setattr(chat_router, "JUDGE_SAMPLE_RATE", 1.0)
    scheduled = []
    monkeypatch.setattr(asyncio, "create_task", lambda coro: scheduled.append(coro) or coro.close())
    chat_router._schedule_eval(1, "q", "ctx", _NO_DOC_MSG, 1)
    assert scheduled == []


def test_kpi_p95_matches_the_eval_report_definition():
    from evals.graders import percentile
    from pipeline.jobs.tasks import _p95

    values = [100, 200, 300, 400, 1000]
    assert _p95(values) == round(percentile([float(v) for v in values], 0.95), 1)


def test_page_footers_and_repeated_blocks_do_not_fail_a_whole_document():
    from validation.ge_validate import chunks_to_dataframe, drop_noise_chunks, validate_document_chunks

    body = "Quy trình đo công suất quang trên ONT trước khi thay thiết bị. " * 3
    df = chunks_to_dataframe(
        [
            {"content": body, "source": "sop.pdf"},
            {"content": "Trang 3", "source": "sop.pdf"},
            {"content": body, "source": "sop.pdf"},
            {"content": body + " Bước 2.", "source": "sop.pdf"},
        ]
    )
    cleaned, notes = drop_noise_chunks(df)
    assert len(cleaned) == 2 and len(notes) == 2
    assert validate_document_chunks(cleaned).success


# ============================================================================ retrieval
def test_unaccented_query_and_joined_error_code_match_the_sop():
    from rag.bm25 import tokenize

    doc = set(tokenize("Xử lý lỗi E-204 mất nguồn trên ONT"))
    assert {"loi", "mat", "nguon"} <= doc
    assert set(tokenize("loi mat nguon")) <= doc
    assert {"e-204", "e204"} <= set(tokenize("E204")) & doc


def test_lexical_fallback_keeps_the_hybrid_ranking_as_the_main_signal(monkeypatch):
    from rag.rerank import HybridRanking, lexical_rerank, rerank_chunks

    monkeypatch.setattr("rag.rerank.COHERE_API_KEY", "", raising=False)
    # Hybrid (RRF) put a semantically matching chunk first even though it shares no words.
    chunks = [("Thiết bị báo đỏ ở cổng quang", "a.pdf", 1)] + [
        (f"đèn LOS kiểm tra {i}", f"{i}.pdf", 1) for i in range(5)
    ]
    assert chunks[0] in lexical_rerank("đèn LOS kiểm tra", chunks, 5, keep_order=True)
    assert chunks[0] in rerank_chunks("đèn LOS kiểm tra", HybridRanking(chunks), top_n=5)
    # Vector-only input (BM25 branch down): word overlap is the only lexical signal, re-sort fully.
    assert chunks[0] not in rerank_chunks("đèn LOS kiểm tra", list(chunks), top_n=5)


def test_technical_rag_keeps_the_best_sections_instead_of_refusing(tmp_path):
    from rag.technical.models import Block
    from rag.technical.store import build_index
    from test_technical_rag import SCOPE, FakeEmbeddings, FakeSelector, bundle, service

    blocks = [
        Block(
            kind="text",
            text=f"E01 bước {i}: kiểm tra nguồn.",
            section=f"procedure-{i}",
            pages=[i],
            single_equipment=True,
        )
        for i in range(1, 7)
    ]
    build_index(tmp_path, [bundle(blocks=blocks)], FakeEmbeddings(), "fake:v1")
    selector = FakeSelector()
    result = service(tmp_path, selector=selector, max_parents=4).answer("E01", SCOPE)
    assert result.status != "abstained", result.reason
    assert 1 <= len(selector.calls[-1]) <= 4


def test_vietnamese_decimal_comma_is_a_decimal():
    from rag.technical.pipeline import anchors, exact_match

    assert anchors("Điện áp 12,5 V có bình thường không?")[1] == {"12.5V"}
    assert exact_match("0,5 A", "dòng 0.5 A") and exact_match("0.5 A", "dòng 0,5 A")
    assert not exact_match("0,5 A", "dòng 5 A")


# ============================================================================ sessions
def _login(client_, db_factory_):  # noqa: F811
    with db_factory_() as db:
        db.add(User(id=31, email="s@example.com", hashed_password=hash_password("Password-123"), role="user"))
        db.commit()
    return client_.post("/auth/login", json={"email": "s@example.com", "password": "Password-123"}).json()[
        "access_token"
    ]


def test_session_upgrade_keeps_the_original_expiry(client, db_factory):  # noqa: F811
    token = _login(client, db_factory)
    original = decode_access_token(token)["exp"]
    time.sleep(1.1)
    upgraded = client.post("/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert upgraded.status_code == 200
    renewed = decode_access_token(client.cookies.get(config.AUTH_COOKIE_NAME))
    assert renewed["exp"] == original, "renewal must not extend a (possibly stolen) token"


def test_logout_revokes_every_token_of_the_user(client, db_factory):  # noqa: F811
    token = _login(client, db_factory)
    other_device = client.post("/auth/login", json={"email": "s@example.com", "password": "Password-123"}).json()[
        "access_token"
    ]
    assert client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    for t in (token, other_device):
        assert client.get("/protected", headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_cross_site_logout_without_csrf_header_only_clears_the_cookie(client, db_factory):  # noqa: F811
    token = _login(client, db_factory)
    client.cookies.set(config.AUTH_COOKIE_NAME, token)
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/protected", headers={"Authorization": f"Bearer {token}"}).status_code == 200


# ============================================================================ cost caps
def test_vietnamese_prompt_token_estimate_is_conservative(monkeypatch):
    from costs.recorder import estimate_tokens
    from rag import llm

    vietnamese = "Kiểm tra đèn LOS trên thiết bị đầu cuối quang. " * 100
    assert estimate_tokens(vietnamese) >= len(vietnamese) / 2.5
    monkeypatch.setattr(llm, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm, "client", lambda: pytest.fail("provider must not be called"))
    with pytest.raises(ValueError):  # 3 chars/token: under the old /4 estimate this passed the cap
        llm.generate_text("ộ" * (config.LLM_MAX_INPUT_TOKENS * 3))


def test_compressed_document_cannot_extract_more_text_than_was_reserved(tmp_path, monkeypatch):
    import zipfile

    from langchain_core.documents import Document

    from costs.budget import ingest_reserve_amount
    from rag import ingest

    doc = tmp_path / "dense.docx"
    with zipfile.ZipFile(doc, "w") as archive:  # stored (ratio 1): passes the zip-bomb check
        archive.writestr("word/document.xml", "x" * 2000)
    size = doc.stat().st_size

    class Loader:
        def __init__(self, path):
            pass

        def load(self):
            return [Document(page_content="chữ " * (size * 3), metadata={})]  # 12 chars per byte

    monkeypatch.setattr(ingest, "Docx2txtLoader", Loader)
    with pytest.raises(ingest.UnsafeDocumentError):
        ingest._load_sync(doc)
    from costs.pricing import compute_cost

    # The reservation must cover the most text ingest will accept for a file of this size.
    accepted_tokens = size * config.MAX_TEXT_CHARS_PER_BYTE / 2.5
    assert ingest_reserve_amount(size) >= compute_cost(config.EMBEDDING_MODEL, int(accepted_tokens))


# ============================================================================ eval split
def _run_evals_module():
    spec = importlib.util.spec_from_file_location("run_evals", ROOT / "backend/scripts/run_evals.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_holdout_is_frozen_by_fingerprint_and_dev_edits_do_not_touch_it(tmp_path):
    module = _run_evals_module()
    source = ROOT / "backend/evals/datasets/fieldops-sop-golden-v1.json"
    dataset = json.loads(source.read_text(encoding="utf-8"))
    path = tmp_path / "d.json"
    path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    frozen = module.holdout_fingerprint(path)
    assert (
        frozen
        == json.loads((ROOT / "backend/evals/baselines/offline-holdout.json").read_text(encoding="utf-8"))[
            "holdout_fingerprint"
        ]
    )
    next(c for c in dataset["cases"] if c.get("split", "dev") == "dev")["query"] += " (sửa)"
    path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    assert module.holdout_fingerprint(path) == frozen
    next(c for c in dataset["cases"] if c.get("split") == "holdout")["query"] += " (sửa)"
    path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    assert module.holdout_fingerprint(path) != frozen


def test_ci_gates_only_on_the_holdout_split():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    gate_lines = [line for line in ci.splitlines() if "run_evals.py" in line]
    assert any("--split holdout" in line and "--no-gates" not in line for line in gate_lines)
    assert all("--split holdout" in line or "--no-gates" in line for line in gate_lines)


# ============================================================================ Render same-origin
def test_render_blueprint_serves_the_api_same_origin():
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    web = render.split("name: rag-web", 1)[1]
    assert "key: NEXT_PUBLIC_API_URL\n        value: /api" in web.replace("\r\n", "\n")
    assert "key: API_PROXY_TARGET" in web
    dockerfile = (ROOT / "frontend/Dockerfile").read_text(encoding="utf-8")
    assert "ARG API_PROXY_TARGET" in dockerfile
