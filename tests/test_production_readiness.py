"""Regression tests for the production-readiness audit (2026-11-17, round 7).
Each test fails on commit 4d7068a."""
from __future__ import annotations

import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from auth import ratelimit
from auth.models import User
from auth.service import hash_password
from test_auth_providers import client, db_factory  # noqa: F401
from test_query_quota import quota_client, quota_db  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


# ============================================================================ API headers / errors / docs
def test_every_api_response_carries_security_headers():
    from security_headers import SecurityHeadersMiddleware

    app = FastAPI()

    @app.get("/x")
    def x():
        return {"ok": True}

    app.add_middleware(SecurityHeadersMiddleware, hsts=True)
    response = TestClient(app).get("/x")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "max-age" in response.headers["strict-transport-security"]
    assert TestClient(app).get("/missing").headers["x-content-type-options"] == "nosniff"


def test_main_app_installs_the_security_headers():
    import main

    response = TestClient(main.app).get("/health")
    assert response.headers.get("x-content-type-options") == "nosniff"


def test_unexpected_errors_return_json_without_details():
    import main

    @main.app.get("/__boom_for_test")
    def boom():
        raise RuntimeError("synthetic-secret /app/data/uploads/12/x.pdf")

    try:
        response = TestClient(main.app, raise_server_exceptions=False).get("/__boom_for_test")
    finally:
        main.app.router.routes[:] = [r for r in main.app.router.routes if getattr(r, "path", "") != "/__boom_for_test"]
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert "detail" in response.json() and "synthetic-secret" not in response.text


def test_api_docs_are_off_in_production(monkeypatch):
    import main

    monkeypatch.setattr(config, "APP_ENV", "production")
    assert main._api_docs_urls() == {"docs_url": None, "redoc_url": None, "openapi_url": None}
    monkeypatch.setattr(config, "APP_ENV", "development")
    assert main._api_docs_urls() == {}


# ============================================================================ production config
def _production(monkeypatch, env="production"):
    monkeypatch.setattr(config, "APP_ENV", env)
    monkeypatch.setattr(config, "SECRET_KEY", "test-only-unique-signing-key-of-32-or-more-characters")
    monkeypatch.setattr(config, "APP_PUBLIC_URL", "https://fieldops.example.com")
    monkeypatch.setattr(config, "API_PUBLIC_URL", "https://fieldops.example.com/api")
    monkeypatch.setenv("CORS_ORIGINS", "https://fieldops.example.com")


def test_unusual_production_env_names_still_get_production_checks(monkeypatch):
    _production(monkeypatch, env="prd")
    monkeypatch.setattr(config, "SECRET_KEY", "dev-secret-change-in-production-32ch")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config.validate_security_config()


@pytest.mark.parametrize("name,value", [("APP_PUBLIC_URL", "http://localhost:3000"),
                                        ("API_PUBLIC_URL", "http://localhost:8000"),
                                        ("APP_PUBLIC_URL", "https://127.0.0.1")])
def test_production_refuses_localhost_public_urls(monkeypatch, name, value):
    _production(monkeypatch)
    monkeypatch.setattr(config, name, value)
    with pytest.raises(RuntimeError, match=name):
        config.validate_security_config()


@pytest.mark.parametrize("origins", ["*", "null", "http://fieldops.example.com"])
def test_production_refuses_wildcard_or_plain_http_cors(monkeypatch, origins):
    _production(monkeypatch)
    monkeypatch.setenv("CORS_ORIGINS", origins)
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        config.validate_security_config()


def test_valid_production_config_starts(monkeypatch):
    _production(monkeypatch)
    config.validate_security_config()


def test_heroku_style_database_url_is_normalised():
    env = {**os.environ, "DATABASE_URL": "postgres://u:p@db.example.com:5432/app", "PYTHONPATH": str(BACKEND)}
    out = subprocess.run([sys.executable, "-c", "import config; print(config.DATABASE_URL)"], cwd=BACKEND,
                         env=env, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "postgresql://u:p@db.example.com:5432/app", out.stderr[-500:]


def test_readiness_reports_an_unreachable_ollama(monkeypatch):
    import main

    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:9")
    assert main._llm_readiness("ollama", "").startswith("unreachable")
    assert main._llm_readiness("gemini", "") == "missing_key"


def test_unexpected_upload_errors_do_not_leak_internals():
    from rag.errors import PublicIngestError
    from rag.ingest import format_ingest_error

    leaked = format_ingest_error(RuntimeError("sqlite3.OperationalError at /app/data/uploads/12/sop.pdf"))
    assert "/app/data" not in leaked and "sqlite" not in leaked
    assert format_ingest_error(PublicIngestError("Định dạng không hỗ trợ: .exe")) == "Định dạng không hỗ trợ: .exe"


# ============================================================================ login rate limits
def _login(c, email, password, ip):
    return c.post("/auth/login", json={"email": email, "password": password}, headers={"X-Forwarded-For": ip})


def test_a_stranger_cannot_lock_someone_else_out(client, db_factory, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ratelimit, "TRUSTED_PROXY_HOPS", 1)
    with db_factory() as db:
        db.add(User(id=41, email="victim@example.com", hashed_password=hash_password("Password-123"), role="user"))
        db.commit()
    for _ in range(12):
        _login(client, "victim@example.com", "wrong-password", "203.0.113.9")
    assert _login(client, "victim@example.com", "wrong-password", "203.0.113.9").status_code == 429
    assert _login(client, "victim@example.com", "Password-123", "198.51.100.7").status_code == 200


def test_real_client_ip_is_read_from_the_right_of_x_forwarded_for(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(ratelimit, "TRUSTED_PROXY_HOPS", 2)
    request = SimpleNamespace(headers={"x-forwarded-for": "6.6.6.6, 198.51.100.7, 10.0.0.2"},
                              client=SimpleNamespace(host="10.0.0.3"))
    assert ratelimit.client_ip(request) == "198.51.100.7", "a spoofed left-most value must be ignored"
    monkeypatch.setattr(ratelimit, "TRUSTED_PROXY_HOPS", 0)
    assert ratelimit.client_ip(request) == "10.0.0.3"


# ============================================================================ database / backups
def test_hot_query_indexes_exist_on_new_and_upgraded_databases(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    import schema_migrations as migrations
    from database import Base

    expected = {name for _, name, _ in migrations._HOT_INDEXES}
    engine = create_engine(f"sqlite:///{(tmp_path / 'a.db').as_posix()}")
    Base.metadata.create_all(engine)

    def names():
        inspector = inspect(engine)
        return {ix["name"] for table in ("messages", "llm_usage_events", "field_service_cases")
                for ix in inspector.get_indexes(table)}

    assert expected <= names(), "fresh databases get the indexes from the models"
    with engine.begin() as conn:                       # an existing database from before migration 7
        for name in expected:
            conn.execute(text(f"DROP INDEX {name}"))
    with engine.begin() as conn:
        migrations._add_hot_query_indexes(conn)
    assert expected <= names()
    engine.dispose()


def test_backup_also_archives_uploaded_documents(tmp_path, monkeypatch):
    from pipeline.jobs import tasks

    data = tmp_path / "data"
    (data / "uploads" / "7").mkdir(parents=True)
    (data / "uploads" / "7" / "sop.txt").write_text("Quy trình", encoding="utf-8")
    monkeypatch.setattr(config, "DATA_ROOT", data)
    monkeypatch.setattr(config, "UPLOAD_DIR", data / "uploads")
    monkeypatch.setattr(config, "CHROMA_DIR", data / "chroma_db")
    monkeypatch.setattr(config, "FIELD_PHOTO_DIR", data / "field_photos")
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://example/none")
    monkeypatch.setenv("BACKUP_DIR", str(data / "backups"))
    count, details, _ = tasks.backup(None)
    archives = [f for f in details["files"] if f.startswith("uploads-")]
    assert archives, details
    with tarfile.open(data / "backups" / archives[0]) as tar:
        assert "uploads/7/sop.txt" in tar.getnames()
    assert "BACKUP_DIR" in details["note"], "a backup on the data volume is flagged"


# ============================================================================ deploy configuration
def test_render_keeps_app_data_in_postgres_and_uses_the_public_api_url():
    text = (ROOT / "render.yaml").read_text(encoding="utf-8").replace("\r\n", "\n")
    api, web = text.split("name: rag-web", 1)
    assert "key: DATABASE_URL\n        fromDatabase:" in api
    assert "key: APP_PUBLIC_URL" in api
    assert "key: LLM_DAILY_BUDGET_USD\n        value: \"2\"" in api
    import yaml

    services = {s["name"]: s for s in yaml.safe_load(text)["services"]}
    proxy = next(v for v in services["rag-web"]["envVars"] if v["key"] == "API_PROXY_TARGET")
    assert proxy == {"key": "API_PROXY_TARGET", "sync": False}, "fromService host is the private hostname"


def test_railway_api_builds_from_the_backend_directory():
    assert not (ROOT / "railway.toml").exists(), "root toml built backend/Dockerfile with the repo root as context"
    toml = (BACKEND / "railway.toml").read_text(encoding="utf-8")
    assert 'dockerfilePath = "Dockerfile"' in toml
    assert "NEXT_PUBLIC_API_URL=/api" in (ROOT / "frontend/railway.toml").read_text(encoding="utf-8")


def test_cloud_compose_caps_ai_spending_by_default():
    compose = (ROOT / "deploy/cloud/docker-compose.yml").read_text(encoding="utf-8")
    assert "${LLM_DAILY_BUDGET_USD:-2}" in compose and "${LLM_USER_DAILY_BUDGET_USD:-0.2}" in compose
    for name in ("LLM_PRICING_JSON", "MAX_UPLOAD_MB", "SMTP_USE_SSL", "EMAIL_SENDER_NAME"):
        assert f"{name}: ${{{name}" in compose, name


def test_production_stack_runs_the_fieldops_scheduler():
    import yaml

    compose = yaml.safe_load((ROOT / "deploy/production/docker-compose.yml").read_text(encoding="utf-8"))
    pipeline = compose["services"]["rag-pipeline"]
    assert pipeline["command"][-2:] == ["pipeline.jobs.runner", "--schedule"]
    assert "rag_data:/app/data" in pipeline["volumes"]
    assert compose["services"]["rag-backend"]["depends_on"]["redis-rag"]["condition"] == "service_healthy"


def test_deploy_workflow_ships_the_commit_ci_tested():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert "workflow_run.event == 'push'" in workflow and "workflow_run.head_sha" in workflow
    assert "DEPLOY_SHA" in (ROOT / "deploy/cloud/deploy.sh").read_text(encoding="utf-8")


def test_images_have_healthchecks_and_the_web_image_is_not_root():
    web = (ROOT / "frontend/Dockerfile").read_text(encoding="utf-8")
    api = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    assert "USER node" in web and "HEALTHCHECK" in web and "HEALTHCHECK" in api


def test_frontend_has_error_boundaries():
    app = ROOT / "frontend/app"
    for path in (app / "error.tsx", app / "(app)/error.tsx", app / "global-error.tsx", app / "not-found.tsx"):
        assert path.exists(), path
    assert (app / "global-error.tsx").read_text(encoding="utf-8").count("<html lang") == 1


def test_static_ips_never_collide_with_dynamically_assigned_ones():
    """On a fixed subnet Docker hands the first free address (.2) to whichever container joins
    first; a container pinned to that address then fails with "Address already in use". Every
    pinned ipv4_address must sit inside the subnet but OUTSIDE the network's dynamic ip_range."""
    import ipaddress

    import yaml

    compose = yaml.safe_load((ROOT / "deploy/production/docker-compose.yml").read_text(encoding="utf-8"))
    networks = compose["networks"]
    pinned = 0
    for name, service in compose["services"].items():
        nets = service.get("networks")
        if not isinstance(nets, dict):
            continue
        for net, conf in nets.items():
            if not conf or "ipv4_address" not in conf:
                continue
            pinned += 1
            ipam = networks[net]["ipam"]["config"][0]
            address = ipaddress.ip_address(conf["ipv4_address"])
            assert address in ipaddress.ip_network(ipam["subnet"]), (name, net)
            assert "ip_range" in ipam, f"{net} has pinned addresses but no ip_range for dynamic ones"
            assert address not in ipaddress.ip_network(ipam["ip_range"]), (name, net, address)
    assert pinned >= 3
    cloud = (ROOT / "deploy/cloud/docker-compose.yml").read_text(encoding="utf-8")
    assert "ipv4_address" not in cloud, "the shared edge network has no fixed subnet; do not pin addresses"


def test_no_env_file_can_enter_a_docker_build_context():
    for directory in ("frontend", "backend"):
        lines = (ROOT / directory / ".dockerignore").read_text(encoding="utf-8").splitlines()
        assert ".env*" in [line.strip() for line in lines], directory


def test_missing_model_key_is_reported_as_unavailable_not_a_server_bug(quota_client, monkeypatch):  # noqa: F811
    import chat.router as chat_router

    client_, _, _ = quota_client
    monkeypatch.setattr(chat_router, "GEMINI_API_KEY", "")
    response = client_.post("/chat/1", json={"question": "Đèn LOS đỏ thì kiểm tra gì?"})
    assert response.status_code == 503 and response.json()["detail"] == "Thiếu GEMINI_API_KEY"


def test_cloud_scheduler_container_does_not_inherit_the_http_healthcheck():
    import yaml

    compose = yaml.safe_load((ROOT / "deploy/cloud/docker-compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["pipeline"]["healthcheck"] == {"disable": True}
