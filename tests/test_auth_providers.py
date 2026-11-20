"""Google Sign-In, xác thực email và rate-limit đăng nhập."""

from datetime import UTC, datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import auth.google as google_auth
from auth.lifecycle import deactivate_account
from auth.models import User
from auth.router import router as auth_router
from auth.service import create_access_token, create_email_verification_token, hash_password
from chat import models as chat_models  # noqa: F401
from database import Base, _enable_sqlite_foreign_keys, get_db
from dependencies import get_current_user
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
    monkeypatch.setattr(google_auth, "GOOGLE_CLIENT_IDS", [CLIENT_ID])
    app = FastAPI()
    app.include_router(auth_router)

    @app.get("/protected")
    def protected(user=Depends(get_current_user)):
        return {"id": user.id}

    def override():
        with db_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c


def _fake_google(monkeypatch, sub="g-1", email="new@gmail.com", verified=True):
    def verify(credential, **_):
        return google_auth.identity_from_claims(
            {
                "iss": "https://accounts.google.com",
                "sub": sub,
                "email": email,
                "email_verified": verified,
                "aud": CLIENT_ID,
            }
        )

    monkeypatch.setattr(google_auth, "verify_id_token", verify)


def _bearer(factory, user_id):
    with factory() as db:
        return {"Authorization": f"Bearer {create_access_token(db.get(User, user_id))}"}


# ------------------------------------------------------------------ Google
def test_google_creates_verified_account_and_first_user_is_admin(client, db_factory, monkeypatch):
    _fake_google(monkeypatch)
    res = client.post("/auth/google", json={"credential": "x" * 40})
    assert res.status_code == 200
    body = res.json()
    assert body["is_new_user"] is True
    assert body["user"]["email_verified"] is True and body["user"]["auth_provider"] == "google"
    assert body["user"]["role"] == "admin"
    again = client.post("/auth/google", json={"credential": "x" * 40}).json()
    assert again["is_new_user"] is False and again["user"]["id"] == body["user"]["id"]


def test_google_links_existing_password_account_case_insensitively(client, db_factory, monkeypatch):
    with db_factory() as db:
        db.add(User(id=5, email="Tech@example.com", hashed_password=hash_password("Password-123"), role="user"))
        db.commit()
    _fake_google(monkeypatch, sub="g-5", email="tech@example.com")
    res = client.post("/auth/google", json={"credential": "x" * 40})
    assert res.status_code == 200 and res.json()["user"]["id"] == 5
    assert res.json()["user"]["has_google"] is True
    # Tài khoản chưa xác minh email: mật khẩu do người đăng ký trước đặt không được giữ quyền
    # sau khi chủ hộp thư (Google đã xác minh) đăng nhập — chống pre-hijack.
    assert client.post("/auth/login", json={"email": "Tech@example.com", "password": "Password-123"}).status_code == 401


def test_google_link_keeps_password_of_already_verified_account(client, db_factory, monkeypatch):
    with db_factory() as db:
        db.add(
            User(
                id=5,
                email="Tech@example.com",
                hashed_password=hash_password("Password-123"),
                role="user",
                email_verified_at=datetime.now(UTC),
            )
        )
        db.commit()
    _fake_google(monkeypatch, sub="g-5", email="tech@example.com")
    assert client.post("/auth/google", json={"credential": "x" * 40}).json()["user"]["id"] == 5
    # Email đã được chính chủ xác minh trước đó: liên kết Google không làm mất mật khẩu.
    assert client.post("/auth/login", json={"email": "Tech@example.com", "password": "Password-123"}).status_code == 200


def test_google_rejects_conflicting_link_and_deactivated_accounts(client, db_factory, monkeypatch):
    with db_factory() as db:
        db.add(User(id=1, email="admin@example.com", hashed_password="x", role="admin"))
        db.add(User(id=6, email="a@example.com", hashed_password="x", google_sub="other-sub"))
        db.commit()
    _fake_google(monkeypatch, sub="g-6", email="a@example.com")
    assert client.post("/auth/google", json={"credential": "x" * 40}).status_code == 403

    _fake_google(monkeypatch, sub="other-sub", email="a@example.com")
    with db_factory() as db:
        deactivate_account(db, 6, actor_id=1)
    # Sau khi vô hiệu hoá, liên kết Google được giải phóng → đăng nhập tạo tài khoản mới.
    res = client.post("/auth/google", json={"credential": "x" * 40})
    assert res.status_code == 200 and res.json()["user"]["id"] != 6


def test_google_only_account_cannot_login_with_password(client, monkeypatch):
    _fake_google(monkeypatch, email="only@gmail.com")
    client.post("/auth/google", json={"credential": "x" * 40})
    res = client.post("/auth/login", json={"email": "only@gmail.com", "password": "!external-login"})
    assert res.status_code == 401


def test_google_disabled_returns_404(client, monkeypatch):
    monkeypatch.setattr(google_auth, "GOOGLE_CLIENT_IDS", [])
    assert client.post("/auth/google", json={"credential": "x" * 40}).status_code == 404
    assert client.get("/auth/config").json()["google_client_id"] is None


def test_identity_requires_google_issuer_and_verified_email():
    base = {"iss": "accounts.google.com", "sub": "1", "email": "a@b.com", "email_verified": True}
    assert google_auth.identity_from_claims(base).email == "a@b.com"
    with pytest.raises(google_auth.GoogleTokenError):
        google_auth.identity_from_claims({**base, "iss": "https://evil.example"})
    with pytest.raises(google_auth.GoogleTokenError):
        google_auth.identity_from_claims({**base, "email_verified": False})


def test_real_signature_audience_and_expiry_checks(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJwks:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": key.public_key()})()

    monkeypatch.setattr(google_auth, "_jwks_client", lambda: FakeJwks())
    now = datetime.now(UTC)
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "42",
        "email": "u@gmail.com",
        "email_verified": True,
        "aud": CLIENT_ID,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    good = jwt.encode(claims, key, algorithm="RS256")
    assert google_auth.verify_id_token(good, client_ids=[CLIENT_ID]).sub == "42"

    wrong_aud = jwt.encode({**claims, "aud": "someone-else"}, key, algorithm="RS256")
    expired = jwt.encode(
        {**claims, "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1)}, key, algorithm="RS256"
    )
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(claims, other_key, algorithm="RS256")
    for token in (wrong_aud, expired, forged):
        with pytest.raises(google_auth.GoogleTokenError):
            google_auth.verify_id_token(token, client_ids=[CLIENT_ID])


# ------------------------------------------------------------------ email verification
def test_register_then_verify_email(client, db_factory):
    res = client.post("/auth/register", json={"email": "v@example.com", "password": "Password-123"})
    user = res.json()["user"]
    assert user["email_verified"] is False
    with db_factory() as db:
        token = create_email_verification_token(db.get(User, user["id"]))
    ok = client.post("/auth/verify-email", json={"token": token})
    assert ok.status_code == 200 and ok.json()["email_verified"] is True
    # Access token không dùng làm token xác thực email được.
    assert client.post("/auth/verify-email", json={"token": res.json()["access_token"]}).status_code == 400


def test_verification_link_dies_when_email_changes(client, db_factory):
    with db_factory() as db:
        db.add(User(id=3, email="old@example.com", hashed_password="x"))
        db.commit()
        token = create_email_verification_token(db.get(User, 3))
        db.get(User, 3).email = "new@example.com"
        db.commit()
    assert client.post("/auth/verify-email", json={"token": token}).status_code == 400


def test_unverified_users_are_blocked_only_when_required(client, db_factory, monkeypatch):
    import auth.email_utils
    import config

    res = client.post("/auth/register", json={"email": "u@example.com", "password": "Password-123"})
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}
    assert client.get("/protected", headers=headers).status_code == 200  # mặc định không bắt buộc

    monkeypatch.setattr(config, "REQUIRE_EMAIL_VERIFICATION", True)
    monkeypatch.setattr(auth.email_utils, "RESEND_API_KEY", "")
    assert client.get("/protected", headers=headers).status_code == 200  # chưa cấu hình email → không khoá

    monkeypatch.setattr(auth.email_utils, "RESEND_API_KEY", "re_test")
    blocked = client.get("/protected", headers=headers)
    assert blocked.status_code == 403 and blocked.headers["x-error-code"] == "email_not_verified"
    assert client.get("/auth/me", headers=headers).status_code == 200  # vẫn xem được hồ sơ
    assert client.get("/auth/config").json()["email_verification_required"] is True

    with db_factory() as db:
        db.get(User, res.json()["user"]["id"]).email_verified_at = datetime.now(UTC)
        db.commit()
    assert client.get("/protected", headers=headers).status_code == 200


# ------------------------------------------------------------------ rate limit
def test_login_is_rate_limited_per_email_and_success_resets(client, db_factory):
    with db_factory() as db:
        db.add(User(id=9, email="rl@example.com", hashed_password=hash_password("Password-123")))
        db.commit()
    for _ in range(9):
        assert client.post("/auth/login", json={"email": "rl@example.com", "password": "bad"}).status_code == 401
    assert client.post("/auth/login", json={"email": "rl@example.com", "password": "Password-123"}).status_code == 200
    for _ in range(10):
        client.post("/auth/login", json={"email": "rl@example.com", "password": "bad"})
    locked = client.post("/auth/login", json={"email": "rl@example.com", "password": "Password-123"})
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0


# ------------------------------------------------------------------ SMTP (Gmail) không cần domain riêng
class _FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None, **_):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        assert password == "abcdefghijklmnop"  # dấu cách của App Password đã được bỏ

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)


def test_smtp_channel_sends_verification_and_reset(client, db_factory, monkeypatch):
    import asyncio

    import auth.email_utils as eu
    import auth.router as router

    monkeypatch.setattr(eu, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(eu, "SMTP_USER", "fieldops.bot@gmail.com")
    monkeypatch.setattr(eu, "SMTP_PASSWORD", "abcd efgh ijkl mnop".replace(" ", ""))
    monkeypatch.setattr(eu, "RESEND_API_KEY", "")
    monkeypatch.setattr(eu.smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.sent = []
    assert eu.email_channel() == "smtp" and eu.is_email_configured()

    res = client.post("/auth/register", json={"email": "smtp@example.com", "password": "Password-123"})
    assert res.status_code == 200
    for _ in range(50):  # email gửi nền
        if _FakeSMTP.sent:
            break
        asyncio.run(asyncio.sleep(0.02))
    assert _FakeSMTP.sent, "verification email not sent"
    body = _FakeSMTP.sent[0].get_body(("html",)).get_content()
    assert "/verify-email?token=" in body and _FakeSMTP.sent[0]["To"] == "smtp@example.com"
    assert client.get("/auth/config").json()["password_reset_enabled"] is True

    _FakeSMTP.sent = []
    assert client.post("/auth/forgot-password", json={"email": "smtp@example.com"}).status_code == 200
    for _ in range(50):  # email đặt lại mật khẩu cũng gửi nền (không lộ email tồn tại qua thời gian)
        if _FakeSMTP.sent:
            break
        asyncio.run(asyncio.sleep(0.02))
    assert "/reset-password?token=" in _FakeSMTP.sent[0].get_body(("html",)).get_content()
    assert router is not None
