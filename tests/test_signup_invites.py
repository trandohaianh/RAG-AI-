"""F1-A: đăng ký chỉ bằng lời mời (SIGNUP_MODE=invite)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import auth.google as google_auth
import config
from admin.router import router as admin_router
from auth.invites import hash_code
from auth.models import SignupInvite, User
from auth.router import router as auth_router
from auth.service import create_access_token
from chat import models as chat_models  # noqa: F401
from config import PRIVACY_POLICY_VERSION as PRIVACY_VERSION
from database import Base, _enable_sqlite_foreign_keys, get_db
from field import models as field_models  # noqa: F401
from saas import models as saas_models  # noqa: F401

CLIENT_ID = "1234-test.apps.googleusercontent.com"


@pytest.fixture
def db_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    yield factory
    engine.dispose()


@pytest.fixture
def client(db_factory, monkeypatch):
    monkeypatch.setattr(config, "SIGNUP_MODE", "invite")
    monkeypatch.setattr(google_auth, "GOOGLE_CLIENT_IDS", [CLIENT_ID])
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(admin_router)

    def override():
        with db_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c


def _register(client, email, code=None):
    body = {"email": email, "password": "secret123", "accepted_privacy_version": PRIVACY_VERSION}
    if code is not None:
        body["invite_code"] = code
    return client.post("/auth/register", json=body)


def _admin_headers(client, db_factory):
    assert _register(client, "admin@x.com").status_code == 200  # tài khoản đầu tiên: không cần mã
    with db_factory() as db:
        admin = db.query(User).filter(User.email == "admin@x.com").one()
        assert admin.role == "admin"
        return {"Authorization": f"Bearer {create_access_token(admin)}"}


def _new_invite(client, headers, **body):
    res = client.post("/admin/invites", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def test_default_mode_is_invite_in_production_and_open_in_dev(monkeypatch):
    import importlib

    monkeypatch.delenv("SIGNUP_MODE", raising=False)
    try:
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("SECRET_KEY", "x" * 40)
        assert importlib.reload(config).SIGNUP_MODE == "invite"
        monkeypatch.setenv("APP_ENV", "development")
        assert importlib.reload(config).SIGNUP_MODE == "open"
        monkeypatch.setenv("SIGNUP_MODE", "bogus")
        with pytest.raises(ValueError):
            importlib.reload(config)
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_auth_config_exposes_signup_mode(client):
    assert client.get("/auth/config").json()["signup_mode"] == "invite"


def test_register_without_invite_is_rejected_after_bootstrap(client, db_factory):
    _admin_headers(client, db_factory)
    res = _register(client, "stranger@x.com")
    assert res.status_code == 403
    assert "lời mời" in res.json()["detail"]
    with db_factory() as db:
        assert db.query(User).filter(User.email == "stranger@x.com").count() == 0


def test_invite_is_single_use_hashed_and_registers_user(client, db_factory):
    headers = _admin_headers(client, db_factory)
    invite = _new_invite(client, headers, note="thợ mới")
    code = invite["code"]
    assert invite["status"] == "active" and code in invite["signup_url"]
    with db_factory() as db:
        row = db.get(SignupInvite, invite["id"])
        assert row.code_hash == hash_code(code) and code not in row.code_hash  # chỉ lưu băm

    ok = _register(client, "worker@x.com", code)
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "user"
    again = _register(client, "other@x.com", code)
    assert again.status_code == 403

    listed = client.get("/admin/invites", headers=headers).json()
    assert listed[0]["status"] == "used" and "code" not in listed[0]
    assert listed[0]["used_by"] == ok.json()["user"]["id"]


def test_wrong_garbage_and_expired_codes_are_rejected(client, db_factory):
    headers = _admin_headers(client, db_factory)
    assert _register(client, "a@x.com", "not-a-real-code").status_code == 403
    invite = _new_invite(client, headers)
    with db_factory() as db:
        row = db.get(SignupInvite, invite["id"])
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert _register(client, "a@x.com", invite["code"]).status_code == 403
    assert client.get("/admin/invites", headers=headers).json()[0]["status"] == "expired"


def test_email_bound_invite_only_works_for_that_email(client, db_factory):
    headers = _admin_headers(client, db_factory)
    invite = _new_invite(client, headers, email="Named@X.com")
    assert _register(client, "someone-else@x.com", invite["code"]).status_code == 403
    assert _register(client, "named@x.com", invite["code"]).status_code == 200


def test_revoked_invite_cannot_be_used_and_revoke_is_idempotent(client, db_factory):
    headers = _admin_headers(client, db_factory)
    invite = _new_invite(client, headers)
    assert client.delete(f"/admin/invites/{invite['id']}", headers=headers).status_code == 200
    assert client.delete(f"/admin/invites/{invite['id']}", headers=headers).status_code == 404
    assert _register(client, "late@x.com", invite["code"]).status_code == 403
    assert client.get("/admin/invites", headers=headers).json()[0]["status"] == "revoked"


def test_failed_registration_does_not_burn_the_invite(client, db_factory):
    headers = _admin_headers(client, db_factory)
    invite = _new_invite(client, headers)
    # Email đã tồn tại → đăng ký thất bại, mã vẫn còn dùng được.
    assert _register(client, "admin@x.com", invite["code"]).status_code == 400
    assert _register(client, "fresh@x.com", invite["code"]).status_code == 200


def test_invite_admin_endpoints_require_admin(client, db_factory):
    headers = _admin_headers(client, db_factory)
    invite = _new_invite(client, headers)
    worker = _register(client, "w@x.com", invite["code"]).json()["user"]
    with db_factory() as db:
        worker_headers = {"Authorization": f"Bearer {create_access_token(db.get(User, worker['id']))}"}
    assert client.post("/admin/invites", json={}, headers=worker_headers).status_code == 403
    assert client.get("/admin/invites", headers=worker_headers).status_code == 403
    assert client.post("/admin/invites", json={"days": 31}, headers=headers).status_code == 422


def _fake_google(monkeypatch, sub, email):
    def verify(credential, **_):
        return google_auth.identity_from_claims(
            {"iss": "https://accounts.google.com", "sub": sub, "email": email, "email_verified": True, "aud": CLIENT_ID}
        )

    monkeypatch.setattr(google_auth, "verify_id_token", verify)


def test_google_new_account_needs_invite_but_existing_account_can_link(client, db_factory, monkeypatch):
    headers = _admin_headers(client, db_factory)
    _fake_google(monkeypatch, "g-new", "newbie@gmail.com")
    assert client.post("/auth/google", json={"credential": "x" * 40}).status_code == 403
    invite = _new_invite(client, headers)
    res = client.post("/auth/google", json={"credential": "x" * 40, "invite_code": invite["code"]})
    assert res.status_code == 200 and res.json()["is_new_user"] is True
    # Tài khoản mật khẩu sẵn có liên kết Google không cần mã.
    _fake_google(monkeypatch, "g-admin", "admin@x.com")
    assert client.post("/auth/google", json={"credential": "x" * 40}).status_code == 200


def test_open_mode_keeps_free_registration(client, db_factory, monkeypatch):
    monkeypatch.setattr(config, "SIGNUP_MODE", "open")
    assert _register(client, "first@x.com").status_code == 200
    assert _register(client, "second@x.com").status_code == 200
