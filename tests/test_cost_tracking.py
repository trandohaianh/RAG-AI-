"""Theo dõi chi phí AI: bảng giá, ghi nhận token, gán user, API tổng hợp và ngân sách."""

import threading
from datetime import UTC
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth.models import User
from auth.service import create_access_token, hash_password
from chat import models as chat_models  # noqa: F401
from costs import pricing, recorder
from costs.budget import enforce_budget
from costs.context import bind_context, current, usage_scope
from costs.models import InfraCost, LlmUsageEvent
from costs.router import router as costs_router
from database import Base, _enable_sqlite_foreign_keys, get_db
from field import models as field_models  # noqa: F401
from saas import models as saas_models  # noqa: F401


@pytest.fixture
def cost_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        hashed = hash_password("Password-123")
        db.add_all(
            [
                User(id=1, email="admin@example.com", hashed_password=hashed, role="admin"),
                User(id=2, email="tech@example.com", hashed_password=hashed, role="user"),
            ]
        )
        db.commit()
    recorder.set_session_factory(factory)
    yield factory
    recorder.set_session_factory(None)
    engine.dispose()


@pytest.fixture
def client(cost_db):
    app = FastAPI()
    app.include_router(costs_router)

    def override():
        with cost_db() as db:
            yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c


def _auth(factory, user_id):
    with factory() as db:
        return {"Authorization": f"Bearer {create_access_token(db.get(User, user_id))}"}


# ---------------------------------------------------------------- pricing
def test_prefix_matching_prefers_more_specific_model():
    assert pricing.price_for("gemini-2.5-flash-lite").input_per_m == 0.10
    assert pricing.price_for("gemini-2.5-flash").input_per_m == 0.30
    assert pricing.price_for("models/gemini-embedding-001").input_per_m == 0.15


def test_thinking_tokens_are_billed_as_output():
    cost = recorder.record_usage(
        provider="gemini",
        model="gemini-2.5-flash",
        input_tokens=1_000_000,
        output_tokens=0,
        thinking_tokens=1_000_000,
    )
    assert cost == pytest.approx(0.30 + 2.50)


def test_rerank_is_priced_per_search_and_unknown_models_are_free():
    assert pricing.compute_cost("rerank-multilingual-v3.0", units=1000) == pytest.approx(2.0)
    assert pricing.compute_cost("some-new-model", 10_000, 10_000) == 0.0
    assert pricing.price_for("some-new-model").known is False


def test_pricing_override_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_PRICING_JSON", '{"gemini-9-ultra": {"input": 10, "output": 20}}')
    pricing.reload_prices()
    try:
        assert pricing.compute_cost("gemini-9-ultra", 1_000_000, 1_000_000) == pytest.approx(30.0)
    finally:
        monkeypatch.delenv("LLM_PRICING_JSON")
        pricing.reload_prices()


# ---------------------------------------------------------------- recording
def test_usage_is_attributed_to_scope_and_survives_thread_hop(cost_db):
    def worker():
        recorder.record_usage(provider="gemini", model="gemini-2.5-flash", input_tokens=100, output_tokens=50)

    with usage_scope(user_id=2, workspace_id=7, feature="chat"):
        thread = threading.Thread(target=bind_context(worker))
        thread.start()
        thread.join()
    assert current().user_id is None

    with cost_db() as db:
        row = db.query(LlmUsageEvent).one()
    assert (row.user_id, row.workspace_id, row.feature) == (2, 7, "chat")
    assert row.cost_usd == pytest.approx(100 * 0.30 / 1e6 + 50 * 2.50 / 1e6)


def test_recording_failure_never_raises(monkeypatch):
    def broken():
        raise RuntimeError("db down")

    recorder.set_session_factory(broken)
    try:
        assert recorder.record_usage(provider="gemini", model="gemini-2.5-flash", input_tokens=10) > 0
    finally:
        recorder.set_session_factory(None)


def test_generate_text_records_provider_usage(cost_db, monkeypatch):
    import rag.llm as llm

    meta = SimpleNamespace(prompt_token_count=1200, candidates_token_count=300, thoughts_token_count=40)
    fake = SimpleNamespace(
        models=SimpleNamespace(generate_content=lambda **_: SimpleNamespace(text="ok", usage_metadata=meta))
    )
    monkeypatch.setattr(llm, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm, "client", lambda: fake)
    with usage_scope(user_id=2):
        assert llm.generate_text("hello", feature="judge") == "ok"
    with cost_db() as db:
        row = db.query(LlmUsageEvent).one()
    assert (row.input_tokens, row.output_tokens, row.thinking_tokens) == (1200, 300, 40)
    assert row.feature == "judge" and row.user_id == 2 and row.estimated is False


def test_stream_records_final_usage_and_partial_streams(cost_db, monkeypatch):
    import rag.llm as llm

    def chunk(text, finish=None, usage=None):
        cand = SimpleNamespace(finish_reason=finish)
        return SimpleNamespace(text=text, candidates=[cand], usage_metadata=usage)

    final = SimpleNamespace(prompt_token_count=500, candidates_token_count=20, thoughts_token_count=0)
    chunks = [chunk("Xin "), chunk("chào", finish="STOP", usage=final)]
    fake = SimpleNamespace(models=SimpleNamespace(generate_content_stream=lambda **_: iter(chunks)))
    monkeypatch.setattr(llm, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm, "client", lambda: fake)

    with usage_scope(user_id=2, feature="chat"):
        assert "".join(llm.stream_text("q")) == "Xin chào"
        partial = llm.stream_text("q2")
        next(partial)
        partial.close()  # client ngắt giữa chừng

    with cost_db() as db:
        rows = db.query(LlmUsageEvent).order_by(LlmUsageEvent.id).all()
    assert [r.status for r in rows] == ["ok", "partial"]
    assert rows[0].input_tokens == 500 and rows[0].estimated is False
    assert rows[1].estimated is True  # không có usage_metadata → ước lượng


def test_metered_embeddings_estimates_tokens(cost_db):
    from rag.embed_utils import MeteredEmbeddings

    class Inner:
        def embed_documents(self, texts, **_):
            return [[0.0] for _ in texts]

        def embed_query(self, text):
            return [0.0]

    emb = MeteredEmbeddings(Inner(), "gemini-embedding-001")
    with usage_scope(user_id=2, workspace_id=3):
        emb.embed_documents(["a" * 400, "b" * 40], batch_size=2)
        emb.embed_query("c" * 8)
    with cost_db() as db:
        rows = db.query(LlmUsageEvent).order_by(LlmUsageEvent.id).all()
    assert [(r.feature, r.input_tokens) for r in rows] == [("embed_ingest", 176), ("embed_query", 4)]  # ký tự / 2.5
    assert all(r.estimated and r.workspace_id == 3 for r in rows)


# ---------------------------------------------------------------- API
def _seed(factory):
    for user_id, feature, cost in ((2, "chat", 0.004), (2, "embed_ingest", 0.001), (1, "judge", 0.002)):
        with usage_scope(user_id=user_id, feature=feature):
            recorder.record_usage(provider="gemini", model="gemini-2.5-flash", input_tokens=10)
        with factory() as db:
            row = db.query(LlmUsageEvent).order_by(LlmUsageEvent.id.desc()).first()
            row.cost_usd = cost
            db.commit()


def test_admin_summary_breaks_down_cost(client, cost_db):
    _seed(cost_db)
    res = client.get("/admin/costs/summary?days=7", headers=_auth(cost_db, 1))
    assert res.status_code == 200
    data = res.json()
    assert data["totals"]["llm_usd"] == pytest.approx(0.007)
    assert data["totals"]["answer_requests"] == 1
    assert data["totals"]["cost_per_answer_usd"] == pytest.approx(0.007)
    assert {f["feature"] for f in data["by_feature"]} == {"chat", "embed_ingest", "judge"}
    assert data["top_users"][0]["email"] == "tech@example.com"
    assert len(data["by_day"]) == 7 and data["by_day"][-1]["cost_usd"] == pytest.approx(0.007)


def test_cost_endpoints_require_admin_and_me_is_scoped(client, cost_db):
    _seed(cost_db)
    tech = _auth(cost_db, 2)
    assert client.get("/admin/costs/summary", headers=tech).status_code == 403
    assert client.get("/admin/costs/events", headers=tech).status_code == 403
    mine = client.get("/costs/me?days=7", headers=tech).json()
    assert mine["totals"]["llm_usd"] == pytest.approx(0.005)
    assert "top_users" not in mine


def test_infra_costs_feed_monthly_projection(client, cost_db):
    from datetime import datetime, timezone

    admin = _auth(cost_db, 1)
    month = datetime.now(UTC).strftime("%Y-%m")
    bad = client.post(
        "/admin/costs/infra", headers=admin, json={"month": "2026-13", "provider": "x", "item": "y", "amount_usd": 1}
    )
    assert bad.status_code == 422
    created = client.post(
        "/admin/costs/infra",
        headers=admin,
        json={"month": month, "provider": "DuckDNS", "item": "Tên miền", "amount_usd": 1.5},
    )
    assert created.status_code == 200
    summary = client.get("/admin/costs/summary", headers=admin).json()
    assert summary["month"]["infra_usd"] == pytest.approx(1.5)
    assert client.delete(f"/admin/costs/infra/{created.json()['id']}", headers=admin).status_code == 200
    with cost_db() as db:
        assert db.query(InfraCost).count() == 0


def test_daily_budget_blocks_new_requests(cost_db, monkeypatch):
    _seed(cost_db)
    # Trần cứng: mỗi request đặt trước một khoản; ở đây đặt nhỏ để kiểm ngưỡng của phần đã chi.
    monkeypatch.setenv("LLM_BUDGET_RESERVE_USD", "0.001")
    monkeypatch.setenv("LLM_USER_DAILY_BUDGET_USD", "0.005")
    with cost_db() as db:
        with pytest.raises(HTTPException) as exc:
            enforce_budget(db, 2)
        assert exc.value.status_code == 429
        enforce_budget(db, 1)  # user 1 mới dùng 0.002
    monkeypatch.setenv("LLM_DAILY_BUDGET_USD", "0.006")
    with cost_db() as db, pytest.raises(HTTPException):
        enforce_budget(db, 1)


def test_metrics_endpoint_requires_token_when_configured(monkeypatch):
    import main

    monkeypatch.setenv("METRICS_TOKEN", "s3cret-token")
    with TestClient(main.app) as c:
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = c.get("/metrics", headers={"Authorization": "Bearer s3cret-token"})
        assert ok.status_code == 200 and "fieldops_llm_cost_usd_total" in ok.text
        import base64

        basic = base64.b64encode(b"grafana:s3cret-token").decode()
        assert c.get("/metrics", headers={"Authorization": f"Basic {basic}"}).status_code == 200


def test_http_metrics_use_route_templates_not_raw_ids():
    import main

    with TestClient(main.app) as c:
        c.get("/conversations/123/messages")
        c.get("/conversations/456/messages")
        text = c.get("/metrics").text
    assert 'endpoint="/conversations/{conv_id}/messages"' in text
    assert "/conversations/123/messages" not in text


def test_readiness_reports_optional_features_and_fails_on_broken_warehouse(monkeypatch):
    import main
    import pipeline.db as pdb

    with TestClient(main.app) as c:
        ok = c.get("/health/ready")
        assert ok.status_code in (200, 503)
        body = ok.json() if ok.status_code == 200 else ok.json()["detail"]
        assert body["features"]["warehouse"] == "disabled"

        monkeypatch.setattr(pdb, "is_medallion_enabled", lambda: True)

        def broken():
            raise ConnectionError("pg down")

        monkeypatch.setattr(pdb, "get_engine", broken)
        res = c.get("/health/ready")
        assert res.status_code == 503
        assert res.json()["detail"]["checks"]["warehouse"] == "error:ConnectionError"
