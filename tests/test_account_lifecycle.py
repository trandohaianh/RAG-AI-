"""Deletion preserves audit identities and cannot transfer stale access."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

import admin.router as admin
import database
from auth.lifecycle import deactivate_account
from auth.models import User
from auth.schemas import UserCreate
from auth.service import authenticate_user, create_access_token, create_reset_token, register_user
from chat.models import Conversation, Message
from database import get_db
from field.models import FieldWorkspace, FieldWorkspaceMember, ServiceCase, ServiceCaseEvent
from field.router import router as field_router
from saas.models import ApiKey, EmbedGuestSession, EmbedToken
from saas.security import hash_secret
from test_auth_security import security_db  # noqa: F401


def seed_workspace(db, *, owner_id=1, member_id=2):
    workspace = FieldWorkspace(name="Lifecycle team", created_by=owner_id)
    db.add(workspace)
    db.flush()
    db.add_all([
        FieldWorkspaceMember(workspace_id=workspace.id, user_id=owner_id, role="owner"),
        FieldWorkspaceMember(workspace_id=workspace.id, user_id=member_id, role="technician"),
    ])
    case = ServiceCase(
        workspace_id=workspace.id, created_by=member_id, assigned_to=member_id,
        title="Audit case", symptom="No signal",
    )
    db.add(case)
    db.flush()
    db.add(ServiceCaseEvent(case_id=case.id, actor_id=member_id, event_type="created", detail="Historic event"))
    db.commit()
    return workspace.id, case.id


def test_account_delete_revokes_access_without_reusing_identity(monkeypatch, tmp_path, security_db):
    with security_db() as db:
        workspace_id, case_id = seed_workspace(db)
        old_user = db.get(User, 2)
        old_access = create_access_token(old_user)
        admin_access = create_access_token(db.get(User, 1))
        create_reset_token(db, old_user)
        db.add(ApiKey(user_id=2, key_prefix="old", key_hash=hash_secret("old-key")))
        embed = EmbedToken(user_id=2, token_hash=hash_secret("old-embed"))
        conv = Conversation(user_id=2, title="Old conversation")
        db.add_all([embed, conv])
        db.flush()
        from datetime import datetime, timedelta, timezone
        db.add(EmbedGuestSession(
            embed_token_id=embed.id, conversation_id=conv.id,
            token_hash=hash_secret("old-session"),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        db.add(Message(conversation_id=conv.id, role="assistant", content="Keep audit"))
        db.commit()

    monkeypatch.setattr(admin, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(admin, "delete_user_collection", lambda _user_id: None)

    async def no_docs(_user_id):
        return []

    monkeypatch.setattr(admin, "list_user_docs", no_docs)
    app = FastAPI()
    app.include_router(admin.router)
    app.include_router(field_router)

    def db_override():
        with security_db() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {admin_access}"}
        assert client.delete("/admin/users/2", headers=headers).status_code == 200
        users = client.get("/admin/users", headers=headers).json()
        assert [user["id"] for user in users] == [1]
        assert client.get("/admin/stats", headers=headers).json()["total_users"] == 1
        assert client.get(f"/field/cases/{case_id}", headers={
            "Authorization": f"Bearer {old_access}",
        }).status_code == 401

        with security_db() as db:
            old = db.get(User, 2)
            assert old.deleted_at is not None
            assert old.email != "tech@example.com"
            assert authenticate_user(db, "tech@example.com", "Password-123") is None
            assert db.query(ApiKey).filter_by(user_id=2).count() == 0
            assert not db.query(EmbedToken).filter_by(user_id=2).one().active
            assert db.query(EmbedGuestSession).count() == 0
            assert db.query(FieldWorkspaceMember).filter_by(user_id=2).count() == 0
            case = db.get(ServiceCase, case_id)
            assert case.created_by == 2
            assert case.assigned_to is None
            assert db.query(ServiceCaseEvent).filter_by(actor_id=2).count() == 1
            replacement = register_user(db, UserCreate(
                email="tech@example.com", password="New-password-123",
            ))
            assert replacement.id != 2
            replacement_access = create_access_token(replacement)
        assert client.get(f"/field/cases/{case_id}", headers={
            "Authorization": f"Bearer {replacement_access}",
        }).status_code == 404


def test_last_owner_of_active_team_cannot_be_deleted(security_db):
    with security_db() as db:
        seed_workspace(db, owner_id=2, member_id=1)
        with pytest.raises(HTTPException) as exc:
            deactivate_account(db, 2, 1)
        assert exc.value.status_code == 409
        assert db.get(User, 2).deleted_at is None
        assert db.query(FieldWorkspaceMember).filter_by(user_id=2, role="owner").count() == 1


def test_solo_owner_can_be_disabled_without_deleting_case_audit(security_db):
    with security_db() as db:
        team = FieldWorkspace(name="Solo team", created_by=2)
        db.add(team)
        db.flush()
        db.add(FieldWorkspaceMember(workspace_id=team.id, user_id=2, role="owner"))
        db.commit()
        team_id = team.id
        deactivate_account(db, 2, 1)
        assert db.get(FieldWorkspace, team_id).created_by == 2
        assert db.get(User, 2).deleted_at is not None
        assert db.query(FieldWorkspaceMember).filter_by(workspace_id=team_id).count() == 0


def test_legacy_sqlite_migration_reserves_orphan_ids_and_preserves_events(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255) UNIQUE, "
            "hashed_password VARCHAR(255), role VARCHAR(20), "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(text("INSERT INTO users (id,email,hashed_password,role) VALUES (1,'live@example.com','unused','admin')"))
        conn.execute(text("CREATE TABLE api_keys (id INTEGER PRIMARY KEY, user_id INTEGER)"))
        conn.execute(text("CREATE TABLE field_workspace_members (id INTEGER PRIMARY KEY, user_id INTEGER)"))
        conn.execute(text("CREATE TABLE field_service_case_events (id INTEGER PRIMARY KEY, actor_id INTEGER, detail TEXT)"))
        conn.execute(text("INSERT INTO api_keys (id,user_id) VALUES (1,7)"))
        conn.execute(text("INSERT INTO field_workspace_members (id,user_id) VALUES (1,7)"))
        conn.execute(text("INSERT INTO field_service_case_events (id,actor_id,detail) VALUES (1,9,'Keep this audit')"))
    monkeypatch.setattr(database, "engine", engine)
    try:
        database._migrate_sqlite_columns()
        database._migrate_sqlite_columns()
        with engine.begin() as conn:
            assert conn.execute(text("SELECT token_version FROM users WHERE id=1")).scalar_one() == 0
            assert conn.execute(text("SELECT COUNT(*) FROM users WHERE id IN (7,9) AND deleted_at IS NOT NULL")).scalar_one() == 2
            assert conn.execute(text("SELECT COUNT(*) FROM api_keys")).scalar_one() == 0
            assert conn.execute(text("SELECT COUNT(*) FROM field_workspace_members")).scalar_one() == 0
            assert conn.execute(text("SELECT detail FROM field_service_case_events WHERE actor_id=9")).scalar_one() == "Keep this audit"
            conn.execute(text("INSERT INTO users (email,hashed_password,role) VALUES ('new@example.com','unused','user')"))
            assert conn.execute(text("SELECT id FROM users WHERE email='new@example.com'")).scalar_one() > 9
    finally:
        engine.dispose()


def test_init_db_upgrades_existing_users_before_auth_queries(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255) UNIQUE, "
            "hashed_password VARCHAR(255), role VARCHAR(20), "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(text(
            "INSERT INTO users (id,email,hashed_password,role) "
            "VALUES (1,'legacy@example.com','unused','admin')"
        ))
    monkeypatch.setattr(database, "engine", engine)
    try:
        database.init_db()
        database.init_db()
        from sqlalchemy.orm import Session
        from auth.models import PasswordResetToken
        with Session(engine) as db:
            user = db.get(User, 1)
            assert user.email == "legacy@example.com"
            assert user.token_version == 0
            assert user.deleted_at is None
            assert db.query(PasswordResetToken).count() == 0
            assert db.query(EmbedGuestSession).count() == 0
    finally:
        engine.dispose()
