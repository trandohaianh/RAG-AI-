"""Field workspace and case lifecycle API tests."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth.models import User
from auth.schemas import CurrentUser
from chat import models as chat_models  # noqa: F401
from database import Base, get_db
from dependencies import get_current_user
from docs.router import router as docs_router
from field import models as field_models  # noqa: F401
from field.router import router as field_router
from rag.cache import cache_key_retrieve
from rag.ingest import _collection_name


@pytest.fixture
def field_client(tmp_path, monkeypatch):
    # Route tests must never inspect or initialise the developer's vector store.
    monkeypatch.setattr("rag.ingest.CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr("docs.router.UPLOAD_DIR", tmp_path / "uploads")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with test_sessions() as db:
        db.add_all(
            [
                User(
                    id=1,
                    email="owner@example.com",
                    hashed_password="not-used",
                    role="user",
                ),
                User(
                    id=2,
                    email="tech@example.com",
                    hashed_password="not-used",
                    role="user",
                ),
            ]
        )
        db.commit()

    app = FastAPI()
    app.include_router(field_router)
    app.include_router(docs_router)
    actor = {"user": CurrentUser(id=1, email="owner@example.com", role="user")}

    def override_db():
        db = test_sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: actor["user"]
    with TestClient(app) as client:
        yield client, actor
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_case_workflow_and_audit_events(field_client):
    client, _ = field_client
    workspace = client.get("/field/workspaces").json()[0]
    created = client.post(
        f"/field/workspaces/{workspace['id']}/cases",
        json={
            "title": "Mất kết nối Internet",
            "symptom": "Đèn PON nhấp nháy đỏ sau khi khởi động lại modem",
            "device_model": "ONT-X1",
            "address": "Số 1 đường Mẫu",
        },
    )
    assert created.status_code == 201
    case_id = created.json()["id"]

    step = client.post(
        f"/field/cases/{case_id}/steps",
        json={
            "title": "Kiểm tra dây quang",
            "instruction": "Kiểm tra đầu nối và tình trạng uốn gập",
            "source": "SOP-FTTH.pdf, trang 4",
        },
    )
    assert step.status_code == 201

    completed = client.patch(
        f"/field/cases/{case_id}/steps/{step.json()['id']}",
        json={"status": "done", "result_note": "Đầu nối đã được kiểm tra"},
    )
    assert completed.status_code == 200
    assert completed.json()["completed_by"] == 1

    detail = client.get(f"/field/cases/{case_id}").json()
    assert detail["steps"][0]["status"] == "done"
    assert {event["event_type"] for event in detail["events"]} >= {
        "created",
        "step_added",
        "step_updated",
    }

    # M12: đóng ca từ giao diện phải qua biên bản (tests/test_field_case_closure.py).
    resolved = client.patch(f"/field/cases/{case_id}", json={"status": "resolved"})
    assert resolved.status_code == 409
    assert client.get(f"/field/cases/{case_id}").json()["status"] != "resolved"


def test_workspace_membership_scopes_case_access(field_client):
    client, actor = field_client
    workspace = client.get("/field/workspaces").json()[0]
    added = client.post(
        f"/field/workspaces/{workspace['id']}/members",
        json={"email": "tech@example.com"},
    )
    assert added.status_code == 201

    created = client.post(
        f"/field/workspaces/{workspace['id']}/cases",
        json={"title": "Kiểm tra modem", "symptom": "Không có tín hiệu"},
    )
    assert created.status_code == 201
    case_id = created.json()["id"]

    actor["user"] = CurrentUser(id=2, email="tech@example.com", role="user")
    assert client.get(f"/field/cases/{case_id}").status_code == 200

    actor["user"] = CurrentUser(id=3, email="outsider@example.com", role="user")
    assert client.get(f"/field/cases/{case_id}").status_code == 404
    assert client.get(f"/field/workspaces/{workspace['id']}/members").status_code == 404
    assert client.get(f"/documents?workspace_id={workspace['id']}").status_code == 404
    assert (
        client.post(
            f"/documents/upload?workspace_id={workspace['id']}",
            files={"file": ("manual.txt", b"sample SOP", "text/plain")},
        ).status_code
        == 404
    )


def test_workspace_knowledge_is_readable_by_members_but_owner_managed(field_client):
    client, actor = field_client
    workspace = client.get("/field/workspaces").json()[0]
    client.post(
        f"/field/workspaces/{workspace['id']}/members",
        json={"email": "tech@example.com"},
    )

    actor["user"] = CurrentUser(id=2, email="tech@example.com", role="user")
    assert client.get(f"/documents?workspace_id={workspace['id']}").status_code == 200
    upload = client.post(
        f"/documents/upload?workspace_id={workspace['id']}",
        files={"file": ("manual.txt", b"sample SOP", "text/plain")},
    )
    assert upload.status_code == 403
    assert client.delete(f"/documents/manual.txt?workspace_id={workspace['id']}").status_code == 403


def test_workspace_retrieval_isolated_from_personal_collections_and_cache():
    assert _collection_name(1) == "user_1"
    assert _collection_name(1, workspace_id=7) == "workspace_7"
    assert _collection_name(1, workspace_id=8) == "workspace_8"
    assert cache_key_retrieve(1, "question", 5, workspace_id=7) != cache_key_retrieve(1, "question", 5, workspace_id=8)
    assert cache_key_retrieve(1, "question", 5) != cache_key_retrieve(1, "question", 5, workspace_id=7)


def test_assignee_must_belong_to_workspace(field_client):
    client, _ = field_client
    workspace = client.get("/field/workspaces").json()[0]
    response = client.post(
        f"/field/workspaces/{workspace['id']}/cases",
        json={
            "title": "Kiểm tra modem",
            "symptom": "Mất kết nối",
            "assigned_to": 2,
        },
    )
    assert response.status_code == 422


def test_assistant_uses_workspace_knowledge(monkeypatch, field_client):
    client, _ = field_client
    workspace = client.get("/field/workspaces").json()[0]
    created = client.post(
        f"/field/workspaces/{workspace['id']}/cases",
        json={"title": "Mất kết nối", "symptom": "Đèn PON đỏ"},
    )
    case_id = created.json()["id"]
    called = {}

    async def fake_answer(user_id, question, history=None, workspace_id=None, **kwargs):
        called["user_id"] = user_id
        called["workspace_id"] = workspace_id
        called["question"] = question
        return "Kiểm tra đầu nối quang.", ["SOP.pdf"]

    monkeypatch.setattr("rag.chain.answer_rag_once", fake_answer)
    response = client.post(
        f"/field/cases/{case_id}/assist",
        json={"question": "Tôi nên kiểm tra gì?"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "Kiểm tra đầu nối quang.",
        "sources": ["SOP.pdf"],
        "evidence": [],
        "citation_audit": None,
    }
    assert called["user_id"] == 1
    assert called["workspace_id"] == workspace["id"]
    assert "Đèn PON đỏ" in called["question"]
    saved = client.get(f"/field/cases/{case_id}").json()
    assert any(event["event_type"] == "assistant_recommendation" for event in saved["events"])


def test_field_client_action_retries_are_idempotent(field_client):
    client, _ = field_client
    workspace = client.get("/field/workspaces").json()[0]
    created = client.post(
        f"/field/workspaces/{workspace['id']}/cases",
        json={"title": "Mất kết nối", "symptom": "Đèn PON đỏ"},
    )
    case_id = created.json()["id"]

    note_payload = {"detail": "Đã kiểm tra nguồn", "client_action_id": "offline-note-001"}
    first_note = client.post(f"/field/cases/{case_id}/events", json=note_payload)
    retry_note = client.post(f"/field/cases/{case_id}/events", json=note_payload)
    assert first_note.status_code == retry_note.status_code == 201
    assert first_note.json()["id"] == retry_note.json()["id"]

    step_payload = {
        "title": "Kiểm tra nguồn",
        "instruction": "Đảm bảo adapter có điện",
        "client_action_id": "offline-step-001",
    }
    first_step = client.post(f"/field/cases/{case_id}/steps", json=step_payload)
    retry_step = client.post(f"/field/cases/{case_id}/steps", json=step_payload)
    assert first_step.status_code == retry_step.status_code == 201
    assert first_step.json()["id"] == retry_step.json()["id"]

    step_id = first_step.json()["id"]
    update_payload = {
        "status": "done",
        "result_note": "Nguồn ổn",
        "client_action_id": "offline-step-update-001",
    }
    first_update = client.patch(f"/field/cases/{case_id}/steps/{step_id}", json=update_payload)
    retry_update = client.patch(f"/field/cases/{case_id}/steps/{step_id}", json=update_payload)
    assert first_update.json()["status"] == retry_update.json()["status"] == "done"

    first_status = client.patch(
        f"/field/cases/{case_id}",
        json={"status": "in_progress", "client_action_id": "offline-status-001"},
    )
    retry_status = client.patch(
        f"/field/cases/{case_id}",
        json={"status": "in_progress", "client_action_id": "offline-status-001"},
    )
    assert first_status.json()["status"] == retry_status.json()["status"] == "in_progress"

    conflicting_replay = client.post(
        f"/field/cases/{case_id}/events",
        json={"detail": "Khác nội dung", "client_action_id": "offline-note-001"},
    )
    assert conflicting_replay.status_code == 409

    events = client.get(f"/field/cases/{case_id}").json()["events"]
    assert sum(event["event_type"] == "field_note" for event in events) == 1
    assert sum(event["event_type"] == "step_added" for event in events) == 1
    assert sum(event["event_type"] == "step_updated" for event in events) == 1
    assert sum(event["event_type"] == "status" for event in events) == 1
