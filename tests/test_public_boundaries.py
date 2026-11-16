"""Public API schemas and warehouse access must enforce their contracts."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.schemas import CurrentUser
from dependencies import get_current_user
import public_api.router as public_api


def test_api_documents_handles_nonempty_dict_results(monkeypatch):
    async def documents(user_id):
        assert user_id == 7
        return [{"name": "manual.pdf", "chunks": 3}]
    monkeypatch.setattr(public_api, "list_user_docs", documents)
    app = FastAPI()
    app.include_router(public_api.router)
    app.dependency_overrides[public_api.get_api_key_user] = lambda: SimpleNamespace(id=7)
    response = TestClient(app).get("/v1/documents")
    assert response.status_code == 200
    assert response.json() == [{"name": "manual.pdf", "chunks": 3, "uploaded_at": None}]


def test_warehouse_snapshot_requires_admin(monkeypatch):
    from main import de_gold_stats
    from dependencies import require_admin
    import pipeline.db as pipeline_db

    monkeypatch.setattr(pipeline_db, "is_medallion_enabled", lambda: False)
    app = FastAPI()
    app.add_api_route("/de/gold/stats", de_gold_stats, methods=["GET"])
    client = TestClient(app)
    assert client.get("/de/gold/stats").status_code == 401
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=1, email="user@example.com", role="user")
    assert client.get("/de/gold/stats").status_code == 403
    app.dependency_overrides[require_admin] = lambda: CurrentUser(id=1, email="admin@example.com", role="admin")
    assert client.get("/de/gold/stats").status_code == 200
