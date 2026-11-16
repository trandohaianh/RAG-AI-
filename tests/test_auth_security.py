"""Authentication regressions against real JWT verification and database state."""
import asyncio
import base64
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth.models import PasswordResetToken, User
from auth.router import router
from auth.service import (
    create_access_token,
    create_reset_token,
    decode_token,
    hash_password,
    reset_password_with_token,
)
from chat import models as chat_models  # noqa: F401
from config import ALGORITHM, SECRET_KEY
from database import Base, _enable_sqlite_foreign_keys, get_db
from dependencies import get_current_user
from field import models as field_models  # noqa: F401
from saas import models as saas_models  # noqa: F401


@pytest.fixture
def security_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        hashed = hash_password("Password-123")
        db.add_all([
            User(id=1, email="owner@example.com", hashed_password=hashed, role="admin"),
            User(id=2, email="tech@example.com", hashed_password=hashed, role="user"),
        ])
        db.commit()
    yield factory
    engine.dispose()


@pytest.fixture
def auth_client(security_db):
    app = FastAPI()
    app.include_router(router)

    def database_override():
        with security_db() as db:
            yield db

    app.dependency_overrides[get_db] = database_override
    with TestClient(app) as client:
        yield client


def test_reset_token_cannot_authenticate_and_access_token_cannot_reset(security_db, auth_client):
    with security_db() as db:
        user = db.get(User, 2)
        access = create_access_token(user)
        reset = create_reset_token(db, user)
        token_row = db.query(PasswordResetToken).one()
        assert token_row.token_hash != decode_token(reset)["jti"]
    assert auth_client.get("/auth/me", headers={"Authorization": f"Bearer {access}"}).status_code == 200
    assert auth_client.get("/auth/me", headers={"Authorization": f"Bearer {reset}"}).status_code == 401
    assert auth_client.post("/auth/reset-password", json={
        "token": access, "password": "New-password-123",
    }).status_code == 400


@pytest.mark.parametrize("claims", [
    {"sub": "tech@example.com"},
    {"sub": "2", "purpose": "reset", "version": 0},
    {"sub": "2", "purpose": "access"},
    {"sub": "2", "purpose": "access", "version": True},
    {"sub": "999999999999999999999", "purpose": "access", "version": 0},
])
def test_legacy_or_malformed_claims_are_rejected(auth_client, claims):
    token = jwt.encode(
        {**claims, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        SECRET_KEY, algorithm=ALGORITHM,
    )
    assert auth_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_successful_reset_is_single_use_and_revokes_all_old_credentials(security_db, auth_client):
    with security_db() as db:
        user = db.get(User, 2)
        access = create_access_token(user)
        first = create_reset_token(db, user)
        second = create_reset_token(db, user)
    assert auth_client.post("/auth/reset-password", json={
        "token": first, "password": "New-password-123",
    }).status_code == 200
    for token in (first, second):
        assert auth_client.post("/auth/reset-password", json={
            "token": token, "password": "Another-password-123",
        }).status_code == 400
    assert auth_client.get("/auth/me", headers={"Authorization": f"Bearer {access}"}).status_code == 401
    assert auth_client.post("/auth/login", json={
        "email": "tech@example.com", "password": "Password-123",
    }).status_code == 401
    login = auth_client.post("/auth/login", json={
        "email": "tech@example.com", "password": "New-password-123",
    })
    assert login.status_code == 200
    assert auth_client.get("/auth/me", headers={
        "Authorization": f"Bearer {login.json()['access_token']}",
    }).status_code == 200


def test_database_expiry_and_unknown_reset_id_are_enforced(security_db):
    with security_db() as db:
        token = create_reset_token(db, db.get(User, 2))
        db.query(PasswordResetToken).update({
            "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
        })
        db.commit()
        assert not reset_password_with_token(db, token, "New-password-123")
        payload = decode_token(token)
        payload["jti"] = "not-issued-by-the-application"
        unknown = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        assert not reset_password_with_token(db, unknown, "New-password-123")


@pytest.mark.parametrize("same_token", [True, False])
def test_concurrent_reset_has_exactly_one_winner(tmp_path, same_token):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reset-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        user = User(email="race@example.com", hashed_password=hash_password("Password-123"))
        db.add(user)
        db.commit()
        first = create_reset_token(db, user)
        second = first if same_token else create_reset_token(db, user)
    ready = Barrier(2)

    def consume(token):
        with factory() as db:
            ready.wait(timeout=10)
            return reset_password_with_token(db, token, "New-password-123")

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(consume, (first, second)))
        assert sorted(results) == [False, True]
        with factory() as db:
            assert db.query(User).one().token_version == 1
    finally:
        engine.dispose()


def test_password_byte_limit_is_validated_before_bcrypt(auth_client):
    response = auth_client.post("/auth/register", json={
        "email": "long@example.com", "password": "á" * 40,
    })
    assert response.status_code == 422


def test_forgot_password_does_not_disclose_missing_email_configuration(monkeypatch, auth_client):
    monkeypatch.setattr("auth.router.is_email_configured", lambda: False)
    known = auth_client.post("/auth/forgot-password", json={"email": "tech@example.com"})
    unknown = auth_client.post("/auth/forgot-password", json={"email": "missing@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


@pytest.mark.parametrize("variant", ["missing_exp", "missing_sub", "expired", "numeric_sub", "unsigned", "wrong_algorithm", "bad_signature"])
def test_invalid_jwt_is_rejected_at_authenticated_endpoint(auth_client, variant):
    claims = {"sub": "2", "purpose": "access", "version": 0,
              "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    key, algorithm = SECRET_KEY, ALGORITHM
    if variant == "missing_exp":
        claims.pop("exp")
    elif variant == "missing_sub":
        claims.pop("sub")
    elif variant == "expired":
        claims["exp"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    elif variant == "numeric_sub":
        claims["sub"] = 2
    elif variant == "unsigned":
        key, algorithm = None, "none"
    elif variant == "wrong_algorithm":
        algorithm = "HS384" if ALGORITHM != "HS384" else "HS256"
    elif variant == "bad_signature":
        key = "different-signing-key-at-least-32-characters"
    token = jwt.encode(claims, key, algorithm=algorithm)
    assert auth_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_existing_typed_hs256_token_remains_compatible(auth_client, monkeypatch):
    # Sign the wire format independently of either JWT library. Existing typed
    # HS256 tokens must survive the library migration until their normal expiry.
    import auth.service as service
    monkeypatch.setattr(service, "ALGORITHM", "HS256")
    def segment(value):
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=")
    header = segment({"alg": "HS256", "typ": "JWT"})
    payload = segment({"sub": "2", "purpose": "access", "version": 0,
                       "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp())})
    message = header + b"." + payload
    signature = base64.urlsafe_b64encode(hmac.new(SECRET_KEY.encode("utf-8"), message, hashlib.sha256).digest()).rstrip(b"=")
    token = (message + b"." + signature).decode("ascii")
    response = auth_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["id"] == 2
