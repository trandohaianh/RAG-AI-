"""Do not serve requests until interrupted document publications are reconciled."""
import asyncio

import pytest

import main
from rag.document_lifecycle import DocumentRecoveryError


@pytest.fixture
def startup(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(main, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(main, "validate_security_config", lambda: calls.append("security"))
    monkeypatch.setattr(main, "init_db", lambda: calls.append("database"))
    monkeypatch.setattr(main, "init_medallion_schema", lambda: calls.append("warehouse"))
    return calls, tmp_path


def test_startup_recovers_before_serving_and_passes_configured_storage_roots(startup, monkeypatch):
    calls, _root = startup

    def recover(index_dir, *, upload_root):
        assert index_dir == main.CHROMA_DIR
        assert upload_root == main.UPLOAD_DIR
        assert index_dir.is_dir() and upload_root.is_dir()
        assert calls == ["security", "database"]
        calls.append("recovery")
        return 2

    monkeypatch.setattr(main, "recover_all_pending", recover)

    async def run():
        async with main.lifespan(main.app):
            calls.append("serving")

    asyncio.run(run())
    assert calls == ["security", "database", "recovery", "warehouse", "serving"]


def test_corrupt_publication_journal_blocks_startup_before_requests_are_served(startup):
    calls, root = startup
    journals = root / ".rag-locks"
    journals.mkdir()
    journal = journals / "broken.journal.json"
    journal.write_text("{incomplete journal", encoding="utf-8")

    async def run():
        async with main.lifespan(main.app):
            pytest.fail("Startup served requests with an unresolved publication")

    with pytest.raises(DocumentRecoveryError):
        asyncio.run(run())
    assert calls == ["security", "database"]
    assert journal.exists(), "Recovery evidence must be retained for repair"
