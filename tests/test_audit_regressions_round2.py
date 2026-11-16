"""Regression tests for the second round of audit fixes (2026-11-16): owner decisions D1–D7 and
issues U1–U19 in reports/01_*.md. Every test reproduces a concrete failure of the pre-fix code.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import billing.router as billing
import chat.router as chat
import embed.router as embed
import rag.chain as chain
from auth.lifecycle import deactivate_account
from auth.models import User
from auth.schemas import CurrentUser
from auth.service import create_access_token, hash_password
from costs import models as cost_models  # noqa: F401  (llm_budget_reservations)
from database import Base, _enable_sqlite_foreign_keys
from evals import graders
from field import integrations, models as field_models, photos
from rag.evidence import audit_citations, capture_evidence
from saas import models as saas_models  # noqa: F401
from test_auth_providers import _fake_google, client, db_factory  # noqa: F401
from test_field_dispatch_photos_integrations import _case, _jpeg, _send, env  # noqa: F401
from test_query_quota import quota_client, quota_db  # noqa: F401
from test_rag_evidence import frames, snapshot

BACKEND = Path(__file__).resolve().parents[1] / "backend"


@pytest.fixture
def sessions():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    import pipeline.jobs.models  # noqa: F401  (pipeline_runs table)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False)
    engine.dispose()


# ============================================================================ auth (D1, U12, U13, U19)
def test_google_link_into_unverified_account_revokes_the_pre_registered_session(client, db_factory, monkeypatch):  # noqa: F811
    with db_factory() as db:
        attacker = User(id=5, email="victim@gmail.com", hashed_password=hash_password("Attacker-123"), role="user")
        db.add(attacker)
        db.commit()
        db.refresh(attacker)
        stolen = create_access_token(attacker)
    assert client.get("/protected", headers={"Authorization": f"Bearer {stolen}"}).status_code == 200
    _fake_google(monkeypatch, sub="g-victim", email="victim@gmail.com")
    assert client.post("/auth/google", json={"credential": "x" * 40}).status_code == 200
    assert client.get("/protected", headers={"Authorization": f"Bearer {stolen}"}).status_code == 401
    assert client.post("/auth/login", json={"email": "victim@gmail.com", "password": "Attacker-123"}).status_code == 401


def test_login_and_team_invite_find_mixed_case_stored_email(client, db_factory, env):  # noqa: F811
    with db_factory() as db:
        db.add(User(id=7, email="Tech@Example.com", hashed_password=hash_password("Password-123"), role="user"))
        db.commit()
    assert client.post("/auth/login", json={"email": "tech@example.com", "password": "Password-123"}).status_code == 200

    field_client, _, ws, sessions_ = env
    with sessions_() as db:
        db.add(User(id=9, email="Mixed@Example.com", hashed_password="x", role="user"))
        db.commit()
    assert field_client.post(f"/field/workspaces/{ws}/members", json={"email": "mixed@example.com"}).status_code == 201


def test_forgot_password_limit_is_per_requester_and_does_not_wait_for_smtp(client, db_factory, monkeypatch):  # noqa: F811
    import auth.router as auth_router

    with db_factory() as db:
        db.add(User(id=3, email="victim@example.com", hashed_password=hash_password("Password-123"), role="user"))
        db.commit()
    monkeypatch.setattr(auth_router, "is_email_configured", lambda: True)

    async def slow_send(*_args):
        await asyncio.sleep(1.5)

    monkeypatch.setattr(auth_router, "send_reset_email", slow_send)
    started = time.perf_counter()
    assert client.post("/auth/forgot-password", json={"email": "victim@example.com"}).status_code == 200
    assert time.perf_counter() - started < 1.0, "known accounts must not answer slower than unknown ones"

    from auth import ratelimit

    ratelimit.reset()
    monkeypatch.setattr(ratelimit, "client_ip", lambda request: "203.0.113.9")       # attacker
    for _ in range(5):
        client.post("/auth/forgot-password", json={"email": "victim@example.com"})
    monkeypatch.setattr(ratelimit, "client_ip", lambda request: "198.51.100.7")      # real owner
    assert client.post("/auth/forgot-password", json={"email": "victim@example.com"}).status_code == 200


def test_deactivating_an_account_cancels_its_stripe_subscription(sessions, monkeypatch):
    cancelled = []
    sdk = SimpleNamespace(Subscription=SimpleNamespace(
        list=lambda **kw: {"data": [{"id": "sub_1"}] if kw["status"] == "active" else []},
        cancel=lambda sub_id: cancelled.append(sub_id)))
    monkeypatch.setattr(billing, "_stripe", lambda: sdk)
    with sessions() as db:
        db.add_all([User(id=1, email="admin@example.com", hashed_password="x", role="admin"),
                    User(id=2, email="payer@example.com", hashed_password="x", stripe_customer_id="cus_2")])
        db.commit()
        deactivate_account(db, 2, actor_id=1)
    assert cancelled == ["sub_1"]


@pytest.mark.parametrize("env_overrides", [{"ALGORITHM": "none"}, {"CHUNK_SIZE": "100", "CHUNK_OVERLAP": "100"}])
def test_invalid_signing_or_chunking_config_fails_at_startup(env_overrides):
    import os

    # Không nạp .env cục bộ của máy chạy test: chỉ biến môi trường của test được tính.
    script = "import dotenv; dotenv.load_dotenv = lambda *a, **k: False; import config"
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=BACKEND,
                            env={**os.environ, **env_overrides}, capture_output=True, text=True)
    assert result.returncode != 0


# ============================================================================ embed (U2)
def test_widget_origin_is_checked_against_the_framing_site(monkeypatch):
    monkeypatch.setattr(embed, "APP_PUBLIC_URL", "https://app.fieldops.vn")
    row = SimpleNamespace(allowed_origins="https://khach.vn")
    embed._check_origin(row, "https://app.fieldops.vn", "https://khach.vn")      # widget on the customer site
    embed._check_origin(row, "https://khach.vn")                                  # customer calls the API directly
    with pytest.raises(Exception):
        embed._check_origin(row, "https://app.fieldops.vn", "https://evil.example")
    with pytest.raises(Exception):
        embed._check_origin(row, "https://evil.example", "https://khach.vn")


# ============================================================================ field roles (D2)
def test_technician_cannot_reassign_or_redispatch_cases_of_others(env):  # noqa: F811
    client, actor, ws, _ = env
    owned = _case(client, ws, title="Ca của u1", assigned_to=1)
    free = _case(client, ws, title="Ca chưa giao")
    actor["user"] = CurrentUser(id=2, email="u2@example.com", role="user")
    assert client.patch(f"/field/cases/{owned['id']}", json={"assigned_to": 2}).status_code == 403
    assert client.patch(f"/field/cases/{owned['id']}", json={"status": "resolved"}).status_code == 403
    assert client.patch(f"/field/cases/{owned['id']}/dispatch", json={"priority": "urgent"}).status_code == 403
    # Claiming an unassigned case and working on it is still allowed.
    assert client.patch(f"/field/cases/{free['id']}", json={"assigned_to": 2}).status_code == 200
    assert client.patch(f"/field/cases/{free['id']}", json={"status": "in_progress"}).status_code == 200


# ============================================================================ webhooks (U4, U5, U10, U11)
class _Client:
    def __init__(self, on_post=None):
        self.calls, self.on_post = [], on_post

    def post(self, url, content, headers, **kwargs):
        self.calls.append((url, headers, kwargs))
        if self.on_post:
            self.on_post()
        return SimpleNamespace(status_code=204)


def _queued_delivery(env_):
    client, _, ws, sessions_ = env_
    integ = client.post(f"/field/workspaces/{ws}/integrations",
                        json={"name": "CRM", "kind": "crm", "outbound_url": "https://crm.example.com/hook"}).json()
    created = _send(client, integ, {"event_id": "e1", "type": "ticket.created", "external_ref": "T-1",
                                    "data": {"title": "Mất mạng", "description": "LOS đỏ"}}).json()
    client.patch(f"/field/cases/{created['case_id']}", json={"status": "in_progress"})
    return integ, sessions_


def test_two_dispatchers_never_send_the_same_delivery_twice(env):  # noqa: F811
    _, sessions_ = _queued_delivery(env)
    later = datetime.now(timezone.utc) + timedelta(minutes=1)

    def concurrent_worker():
        with sessions_() as other:
            integrations.dispatch_due(other, client=second, now=later)

    second = _Client()
    first = _Client(on_post=concurrent_worker)
    with sessions_() as db:
        integrations.dispatch_due(db, client=first, now=later)
    assert len(first.calls) + len(second.calls) == 1


def test_outbound_request_is_pinned_to_the_checked_ip(env, monkeypatch):  # noqa: F811
    _, sessions_ = _queued_delivery(env)
    monkeypatch.delenv("FIELD_WEBHOOK_ALLOW_PRIVATE", raising=False)
    monkeypatch.setattr(integrations.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    client = _Client()
    with sessions_() as db:
        integrations.dispatch_due(db, client=client, now=datetime.now(timezone.utc) + timedelta(minutes=1))
    url, headers, kwargs = client.calls[0]
    assert url.startswith("https://93.184.216.34/"), "connect to the validated address, not a re-resolved one"
    assert headers["Host"] == "crm.example.com" and kwargs["extensions"]["sni_hostname"] == "crm.example.com"


def test_delivered_webhook_cannot_be_retried(env):  # noqa: F811
    integ, sessions_ = _queued_delivery(env)
    client = env[0]
    with sessions_() as db:
        integrations.dispatch_due(db, client=_Client(), now=datetime.now(timezone.utc) + timedelta(minutes=1))
        delivery = db.query(field_models.FieldWebhookDelivery).filter_by(status="delivered").first()
    assert client.post(f"/field/integrations/{integ['id']}/deliveries/{delivery.id}/retry").status_code == 409


def test_inbound_webhook_rejects_oversized_body_before_reading_it(env):  # noqa: F811
    client, _, ws, _ = env
    integ = client.post(f"/field/workspaces/{ws}/integrations", json={"name": "CRM", "kind": "crm"}).json()
    path = integ["inbound_url"].replace("http://testserver", "")
    read = []

    def body():
        read.append(1)
        yield b"x" * 1024

    response = client.post(path, content=body(), headers={"Content-Length": str(10 * 1024 * 1024)})
    assert response.status_code == 413 and not read


def test_replayed_action_id_for_another_checklist_step_is_a_conflict(env):  # noqa: F811
    client, _, ws, _ = env
    case = _case(client, ws)
    first = client.post(f"/field/cases/{case['id']}/steps", json={"title": "Bước A"}).json()
    second = client.post(f"/field/cases/{case['id']}/steps", json={"title": "Bước B"}).json()
    body = {"status": "done", "client_action_id": "act-123456"}
    assert client.patch(f"/field/cases/{case['id']}/steps/{first['id']}", json=body).status_code == 200
    reused = client.patch(f"/field/cases/{case['id']}/steps/{second['id']}", json=body)
    assert reused.status_code == 409, "different step: the edit must not be silently dropped as 'already applied'"


def test_concurrent_retry_with_the_same_action_id_replays_instead_of_500(env, monkeypatch):  # noqa: F811
    from field import router as field_router

    client, _, ws, sessions_ = env
    case = _case(client, ws)
    body = field_router.CaseUpdate(status="in_progress", client_action_id="act-race-1")
    with sessions_() as other:
        other.add(field_models.FieldClientAction(client_action_id="act-race-1", case_id=case["id"], actor_id=1,
                                                 action_type="case_update",
                                                 request_hash=field_router.hashlib.sha256(json.dumps(
                                                     {"status": "in_progress"}, sort_keys=True,
                                                     separators=(",", ":")).encode()).hexdigest()))
        other.commit()
    with sessions_() as db:
        real_get = db.get
        calls = {"n": 0}

        def stale_get(model, key, **kw):   # the first lookup ran before the other request committed
            calls["n"] += 1
            return None if model is field_models.FieldClientAction and calls["n"] == 1 else real_get(model, key, **kw)

        monkeypatch.setattr(db, "get", stale_get)
        action, replay = field_router._claim_client_action(
            db, client_action_id="act-race-1", case_id=case["id"], actor_id=1,
            action_type="case_update", request_body=body)
    assert replay is True and action.client_action_id == "act-race-1"


# ============================================================================ photos (U6)
def test_photo_limit_is_rechecked_after_slow_processing(env, monkeypatch):  # noqa: F811
    client, _, ws, sessions_ = env
    case = _case(client, ws)
    monkeypatch.setattr("config.FIELD_PHOTO_MAX_PER_CASE", 1)
    real = photos.process_image

    def process_while_another_upload_commits(data):
        result = real(data)
        with sessions_() as other:      # the parallel upload finishes first
            other.add(field_models.ServiceCasePhoto(
                case_id=case["id"], uploaded_by=1, storage_key=f"{ws}/{case['id']}/other", sha256="0" * 64,
                content_type="image/jpeg", size_bytes=10, width=1, height=1, caption=""))
            other.commit()
        return result

    monkeypatch.setattr(photos, "process_image", process_while_another_upload_commits)
    response = client.post(f"/field/cases/{case['id']}/photos", files={"file": ("a.jpg", _jpeg(), "image/jpeg")})
    assert response.status_code == 409
    with sessions_() as db:
        assert db.query(field_models.ServiceCasePhoto).filter_by(case_id=case["id"]).count() == 1


# ============================================================================ cost limits (U7)
def test_document_upload_respects_the_daily_ai_budget(monkeypatch, tmp_path, sessions):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import docs.router as docs_router
    from database import get_db
    from dependencies import get_current_user

    ingested = []

    async def fake_ingest(*args, **kwargs):
        ingested.append(args)
        return 1

    monkeypatch.setattr(docs_router, "ingest_file", fake_ingest)
    monkeypatch.setattr(docs_router, "UPLOAD_DIR", tmp_path)
    monkeypatch.setenv("LLM_USER_DAILY_BUDGET_USD", "1")
    monkeypatch.setattr("costs.budget.spent_today", lambda db, user_id=None: 5.0)
    app = FastAPI()
    app.include_router(docs_router.router)

    def override_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=3, email="u@example.com", role="user")
    with TestClient(app) as c:
        response = c.post("/documents/upload", files={"file": ("sop.txt", b"Quy trinh", "text/plain")})
    assert response.status_code == 429 and ingested == []


# ============================================================================ RAG (D3, U17)
@pytest.mark.parametrize("tokens", [["Đúng ", "【E9", "99】 sai"], ["Đúng 【", "E999", "】"]])
def test_invented_citation_label_is_never_streamed(quota_client, monkeypatch, tokens):  # noqa: F811
    client, _, _ = quota_client

    async def retrieve(*args, **kwargs):
        return snapshot()

    monkeypatch.setattr(chain, "retrieve", retrieve)
    monkeypatch.setattr(chain, "_iter_tokens", lambda prompt: iter(tokens))
    monkeypatch.setattr(chat, "stream_rag_answer", chain.stream_rag_answer)
    events = frames(client.post("/chat/1", json={"question": "Kiểm tra?"}))
    streamed = "".join(e.get("token", "") for e in events)
    assert "E999" not in streamed and "【" not in streamed
    assert any("error" in e for e in events) and not any(e.get("done") for e in events)


def test_valid_label_split_across_tokens_still_streams_the_whole_answer(quota_client, monkeypatch):  # noqa: F811
    client, _, _ = quota_client

    async def retrieve(*args, **kwargs):
        return snapshot()

    monkeypatch.setattr(chain, "retrieve", retrieve)
    monkeypatch.setattr(chain, "_iter_tokens", lambda prompt: iter(["Theo nguồn 【", "E1", "】 kiểm tra ONT."]))
    monkeypatch.setattr(chat, "stream_rag_answer", chain.stream_rag_answer)
    events = frames(client.post("/chat/1", json={"question": "Kiểm tra?"}))
    assert "".join(e.get("token", "") for e in events) == "Theo nguồn 【E1】 kiểm tra ONT."
    assert any(e.get("done") for e in events)


@pytest.mark.parametrize("answer", ["Nguồn 【 E9】", "Nguồn 【e1】", "Nguồn ［E7］"])
def test_malformed_citation_labels_are_invalid_not_uncited(answer):
    assert audit_citations(answer, capture_evidence(snapshot()))["status"] == "invalid"


# ============================================================================ evals (U14)
def test_negated_fact_does_not_count_as_covered_and_safety_warning_is_not_forbidden():
    coverage, _ = graders.fact_coverage("Tuyệt đối không được tắt nguồn ONT khi đang cập nhật.", [["tắt nguồn"]])
    assert coverage == 0.0
    assert graders.forbidden_hits("Không được tắt nguồn khi đang cập nhật firmware.", ["tắt nguồn"]) == []
    assert graders.fact_coverage("Chọn đúng cổng PON rồi kiểm tra.", [["cổng"]])[0] == 1.0


def test_cited_answer_with_a_hedge_is_not_a_refusal():
    assert graders.is_refusal("Không thể trả lời chắc chắn, nhưng hãy reset ONT 【E1】.") is False
    assert graders.is_refusal("Thông tin này không có trong tài liệu đã upload.") is True


def test_eval_judge_scores_are_clamped_and_k_cannot_exceed_top_k(monkeypatch):
    from evals import harness

    monkeypatch.setattr("rag.llm.generate_text", lambda *a, **k: '{"faithfulness": 7, "relevancy": -2}')
    assert harness._judge("q", "c", "a") == {"faithfulness": 1.0, "relevancy": 0.0}
    monkeypatch.setattr(harness, "_install_providers", lambda ctx: None)
    with pytest.raises(ValueError):
        harness.run_suite(SimpleNamespace(mode="offline"), Path("unused.json"), k=99)


# ============================================================================ reporting / pipeline (U8, U9, U11, U15)
def test_daily_buckets_use_utc_on_postgresql():
    from sqlalchemy.dialects import postgresql

    from costs.models import LlmUsageEvent
    from database import utc_date

    fake_db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    sql = str(utc_date(LlmUsageEvent.created_at, fake_db).compile(dialect=postgresql.dialect()))
    assert "timezone" in sql.lower()


def test_interrupted_pipeline_runs_are_failed_and_db_outage_does_not_raise(sessions):
    from pipeline.jobs import runner
    from pipeline.jobs.models import PipelineRun

    with sessions() as db:
        db.add(PipelineRun(job="backup", status="running", started_at=datetime.now(timezone.utc) - timedelta(days=1)))
        db.commit()
    assert runner.fail_interrupted_runs(session_factory=sessions) == 1
    with sessions() as db:
        assert db.query(PipelineRun).one().status == "fail"

    def broken_factory():
        raise RuntimeError("database unavailable")

    assert runner.run_job("data_quality", session_factory=broken_factory)["status"] == "fail"


def test_daily_ingest_fails_loudly_when_every_file_fails(monkeypatch):
    from pipeline.jobs import daily_ingest

    finished = []
    monkeypatch.setattr(daily_ingest, "is_medallion_enabled", lambda: True)
    monkeypatch.setattr(daily_ingest, "init_medallion_schema", lambda: None)
    monkeypatch.setattr(daily_ingest, "start_ingest_run", lambda run_id: 1)
    monkeypatch.setattr(daily_ingest, "discover_upload_files", lambda root: [(1, Path("a.txt")), (1, Path("b.txt"))])
    monkeypatch.setattr(daily_ingest, "finish_ingest_run", lambda run_id, **kw: finished.append(kw["status"]))

    async def broken(*args, **kwargs):
        raise RuntimeError("embedding quota")

    monkeypatch.setattr(daily_ingest, "ingest_file", broken)
    with pytest.raises(RuntimeError):
        asyncio.run(daily_ingest._run("run-1"))
    assert finished == ["failed"]


def test_gold_daily_stats_bucket_by_utc_day(monkeypatch):
    import pipeline.gold as gold

    executed = []

    @contextmanager
    def scope():
        yield SimpleNamespace(execute=lambda sql, params=None: executed.append(str(sql)) or SimpleNamespace(
            fetchone=lambda: (0, 0, 0, 0), mappings=lambda: SimpleNamespace(first=lambda: {})))

    monkeypatch.setattr(gold, "session_scope", scope)
    try:
        gold.upsert_daily_stats()
    except Exception:
        pass
    assert executed and "AT TIME ZONE 'UTC'" in executed[0]


def test_dead_letter_gauge_is_exported_by_the_api(env, monkeypatch):  # noqa: F811
    import metrics

    integ, sessions_ = _queued_delivery(env)
    with sessions_() as db:
        db.query(field_models.FieldWebhookDelivery).update({"status": "dead"})
        db.commit()
    monkeypatch.setattr("database.SessionLocal", sessions_)
    families = {f.name: f for f in metrics._PipelineCollector().collect()}
    samples = {s.labels["status"]: s.value for s in families["fieldops_field_webhook_deliveries"].samples}
    assert samples["dead"] == 1


# ============================================================================ deployment (D7, U18)
def test_render_blueprint_runs_the_api_in_production_mode():
    text = (BACKEND.parent / "render.yaml").read_text(encoding="utf-8")
    assert "key: APP_ENV" in text and "value: production" in text and "key: METRICS_TOKEN" in text


def test_cloud_compose_requires_acme_email():
    text = (BACKEND.parent / "deploy/cloud/docker-compose.yml").read_text(encoding="utf-8")
    assert "ACME_EMAIL: ${ACME_EMAIL:?" in text
