"""Bước 7 (P1): đồng ý chính sách, tải dữ liệu của tôi, xoá tài khoản (docs/BAO_MAT_VA_DU_LIEU_CA_NHAN.md)."""

from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import ratelimit
from auth.models import User, UserConsent
from auth.privacy import router as privacy_router
from auth.router import router as auth_router
from auth.service import _UNUSABLE_PASSWORD, create_access_token
from chat import models as chat_models
from config import PRIVACY_POLICY_VERSION as VERSION
from database import Base, _enable_sqlite_foreign_keys, get_db
from field import models as fm
from field import photos
from field.router import router as field_router
from saas import models as saas_models  # noqa: F401

PASSWORD = "Mat-khau-thu-nghiem-1"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("config.FIELD_PHOTO_DIR", tmp_path / "photos")
    monkeypatch.setattr("config.UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr("config.CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr("config.API_PUBLIC_URL", "http://testserver")
    ratelimit.reset()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    app = FastAPI()
    for r in (auth_router, privacy_router, field_router, photos.router):
        app.include_router(r)

    def override_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client, sessions
    engine.dispose()


def _register(client, email):
    res = client.post(
        "/auth/register", json={"email": email, "password": PASSWORD, "accepted_privacy_version": VERSION}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _workspace(client, h):
    return client.get("/field/workspaces", headers=h).json()[0]["id"]


def _jpeg():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (1, 2, 3)).save(buf, "JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------- đồng ý
def test_register_requires_current_policy_and_records_consent(env):
    client, sessions = env
    base = {"email": "a@example.com", "password": PASSWORD}
    assert client.post("/auth/register", json=base).status_code == 422
    old = client.post("/auth/register", json={**base, "accepted_privacy_version": "2020-01-01"})
    assert old.status_code == 422 and "Chính sách" in old.json()["detail"]
    h = _register(client, "a@example.com")
    me = client.get("/auth/me", headers=h).json()
    assert me["privacy_version"] == VERSION == me["privacy_version_current"]
    with sessions() as db:
        [consent] = db.scalars(select(UserConsent)).all()
        assert (consent.version, consent.source) == (VERSION, "register") and consent.accepted_at is not None


def test_existing_user_accepts_policy_once(env):
    client, sessions = env
    with sessions() as db:
        user = User(email="old@example.com", hashed_password="x", role="user")
        db.add(user)
        db.commit()
        h = {"Authorization": f"Bearer {create_access_token(user)}"}
    assert client.get("/auth/me", headers=h).json()["privacy_version"] is None  # app sẽ hỏi lại
    assert client.post("/auth/me/consent", json={"version": "cu"}, headers=h).status_code == 422
    assert client.post("/auth/me/consent", json={"version": VERSION}, headers=h).json()["privacy_version"] == VERSION
    client.post("/auth/me/consent", json={"version": VERSION}, headers=h)
    with sessions() as db:
        assert [(c.source, c.version) for c in db.scalars(select(UserConsent))] == [("prompt", VERSION)]


# ---------------------------------------------------------------- tải dữ liệu
def test_export_contains_only_my_personal_data(env):
    client, sessions = env
    mine = _register(client, "me@example.com")
    other = _register(client, "other@example.com")
    ws = _workspace(client, mine)
    client.post(f"/field/workspaces/{ws}/cases", json={"title": "Ca của tôi", "symptom": "Mất mạng"}, headers=mine)
    _workspace(client, other)
    with sessions() as db:
        me_id = db.scalar(select(User.id).where(User.email == "me@example.com"))
        conv = chat_models.Conversation(user_id=me_id, title="Hỏi SOP")
        db.add(conv)
        db.flush()
        db.add(chat_models.Message(conversation_id=conv.id, role="user", content="Đèn LOS đỏ là gì?"))
        db.commit()
    res = client.get("/auth/me/export", headers=mine)
    assert res.status_code == 200 and "attachment" in res.headers["content-disposition"]
    data = res.json()
    assert data["account"]["email"] == "me@example.com" and data["consents"][0]["version"] == VERSION
    assert [t["role"] for t in data["teams"]] == ["owner"]
    assert data["case_activity"][0]["type"] == "created"
    assert data["assistant_conversations"][0]["messages"][0]["content"] == "Đèn LOS đỏ là gì?"
    assert "other@example.com" not in res.text


# ---------------------------------------------------------------- xoá tài khoản
def test_delete_needs_confirm_word_and_password(env):
    client, sessions = env
    h = _register(client, "d@example.com")
    assert client.post("/auth/me/delete", json={"confirm": "ok", "password": PASSWORD}, headers=h).status_code == 422
    assert client.post("/auth/me/delete", json={"confirm": "XOA", "password": "sai"}, headers=h).status_code == 403
    assert client.get("/auth/me", headers=h).status_code == 200  # chưa xoá gì


def test_deleting_a_solo_account_removes_team_cases_photos_and_personal_data(env, tmp_path):
    client, sessions = env
    h = _register(client, "solo@example.com")
    ws = _workspace(client, h)
    case = client.post(
        f"/field/workspaces/{ws}/cases",
        json={"title": "Ca sửa", "symptom": "Mất mạng", "customer_label": "Nguyễn Văn A", "address": "12 Lê Lợi"},
        headers=h,
    ).json()
    up = client.post(f"/field/cases/{case['id']}/photos", files={"file": ("a.jpg", _jpeg(), "image/jpeg")}, headers=h)
    assert up.status_code == 201, up.text
    assert any((tmp_path / "photos").rglob("*"))
    with sessions() as db:
        uid = db.scalar(select(User.id).where(User.email == "solo@example.com"))
        db.add(chat_models.Conversation(user_id=uid, title="x"))
        db.add(fm.FieldPushDevice(user_id=uid, token="ExponentPushToken[solo-device-001]"))
        db.commit()
    preview = client.get("/auth/me/delete-preview", headers=h).json()
    assert preview["teams_deleted"][0]["cases"] == 1 and preview["requires_password"] is True

    res = client.post("/auth/me/delete", json={"confirm": "xoa", "password": PASSWORD}, headers=h)
    assert res.status_code == 200, res.text
    assert client.get("/auth/me", headers=h).status_code == 401  # phiên cũ bị thu hồi
    login = client.post("/auth/login", json={"email": "solo@example.com", "password": PASSWORD})
    assert login.status_code == 401
    with sessions() as db:
        user = db.get(User, uid)
        assert user.deleted_at is not None and user.email.endswith("@deleted.invalid")
        assert user.privacy_version is None
        for model in (
            fm.FieldWorkspace,
            fm.ServiceCase,
            fm.ServiceCasePhoto,
            fm.FieldPushDevice,
            chat_models.Conversation,
            UserConsent,
        ):
            assert db.scalars(select(model)).all() == [], model.__name__
    assert not [p for p in (tmp_path / "photos").rglob("*") if p.is_file()]  # tệp ảnh đã xoá
    # Email dùng lại để đăng ký mới được.
    _register(client, "solo@example.com")


def test_last_owner_of_a_team_with_members_must_hand_over_first(env):
    client, sessions = env
    owner = _register(client, "boss@example.com")
    _register(client, "tech@example.com")
    ws = _workspace(client, owner)
    client.post(f"/field/workspaces/{ws}/members", json={"email": "tech@example.com"}, headers=owner)
    assert client.get("/auth/me/delete-preview", headers=owner).json()["teams_blocked"][0]["id"] == ws
    res = client.post("/auth/me/delete", json={"confirm": "XOA", "password": PASSWORD}, headers=owner)
    assert res.status_code == 409 and "chuyển quyền" in res.json()["detail"]
    assert client.get("/auth/me", headers=owner).status_code == 200
    with sessions() as db:
        assert db.get(fm.FieldWorkspace, ws) is not None


def test_member_leaves_the_team_and_its_cases_stay(env):
    client, sessions = env
    owner = _register(client, "boss2@example.com")
    tech = _register(client, "tech2@example.com")
    ws = _workspace(client, owner)
    client.post(f"/field/workspaces/{ws}/members", json={"email": "tech2@example.com"}, headers=owner)
    with sessions() as db:
        tech_id = db.scalar(select(User.id).where(User.email == "tech2@example.com"))
    case = client.post(
        f"/field/workspaces/{ws}/cases",
        json={"title": "Ca sửa", "symptom": "x x x", "assigned_to": tech_id},
        headers=owner,
    ).json()
    preview = client.get("/auth/me/delete-preview", headers=tech).json()
    assert [t["id"] for t in preview["teams_left"]] == [ws]
    assert (
        client.post("/auth/me/delete", json={"confirm": "XOA", "password": PASSWORD}, headers=tech).status_code == 200
    )
    with sessions() as db:
        kept = db.get(fm.ServiceCase, case["id"])
        assert kept is not None and kept.assigned_to is None  # dữ liệu của đội vẫn còn
        assert db.get(fm.FieldWorkspace, ws) is not None


def test_google_only_account_deletes_without_password(env):
    client, sessions = env
    with sessions() as db:
        user = User(email="g@example.com", hashed_password=_UNUSABLE_PASSWORD, role="user", auth_provider="google")
        db.add(user)
        db.commit()
        h = {"Authorization": f"Bearer {create_access_token(user)}"}
    assert client.get("/auth/me/delete-preview", headers=h).json()["requires_password"] is False
    assert client.post("/auth/me/delete", json={"confirm": "XOA"}, headers=h).status_code == 200
