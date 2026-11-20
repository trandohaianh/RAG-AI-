"""Failure, concurrency and retrieval regressions for document publication."""

from __future__ import annotations

import asyncio
import copy
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from langchain_core.documents import Document

import rag.cache as cache
import rag.ingest as ingest
import rag.retriever as retriever
from rag.document_lifecycle import advance_revision, read_revision, storage_lock


class FakeCollection:
    def __init__(self):
        self.rows = {
            "legacy-0": ("old revision", {"source": "manual.txt"}, [1.0, 0.0]),
            "legacy-1": ("old extra chunk", {"source": "manual.txt"}, [0.5, 0.5]),
            "other": ("unrelated document", {"source": "other.txt"}, [0.0, 1.0]),
        }
        self.fail_add = False
        self.fail_retire = False

    def get(self, where=None, include=None):
        rows = [(key, row) for key, row in self.rows.items() if not where or row[1].get("source") == where["source"]]
        return copy.deepcopy(
            {
                "ids": [key for key, _ in rows],
                "documents": [row[0] for _, row in rows],
                "metadatas": [row[1] for _, row in rows],
                "embeddings": [row[2] for _, row in rows],
            }
        )

    def add(self, ids, documents, metadatas, embeddings):
        for key, text, meta, vector in zip(ids, documents, metadatas, embeddings, strict=False):
            assert key not in self.rows
            self.rows[key] = (text, meta, vector)
            if self.fail_add:
                self.fail_add = False
                raise RuntimeError("partial add failure")

    def upsert(self, ids, documents, metadatas, embeddings):
        for key, text, meta, vector in zip(ids, documents, metadatas, embeddings, strict=False):
            self.rows[key] = (text, meta, vector)

    def delete(self, ids):
        for key in ids:
            self.rows.pop(key, None)
            if self.fail_retire:
                self.fail_retire = False
                raise RuntimeError("partial retire failure")


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    import pipeline.db
    import rag.document_lifecycle as lifecycle

    collection = FakeCollection()
    destination = tmp_path / "uploads" / "1" / "manual.txt"
    destination.parent.mkdir(parents=True)
    destination.write_text("old revision", encoding="utf-8")
    index_dir = tmp_path / "chroma"
    monkeypatch.setattr(lifecycle, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(ingest, "CHROMA_DIR", index_dir)
    monkeypatch.setattr(retriever, "CHROMA_DIR", index_dir)
    monkeypatch.setattr(pipeline.db, "is_medallion_enabled", lambda: False)

    class Client:
        def get_or_create_collection(self, name):
            return collection

        def get_collection(self, name):
            return collection

    monkeypatch.setattr(ingest.chromadb, "PersistentClient", lambda **kw: Client())
    monkeypatch.setattr(ingest, "_embeddings", lambda: object())
    monkeypatch.setattr(ingest, "embed_texts_with_retry", lambda model, texts: [[1.0, 1.0] for _ in texts])
    monkeypatch.setattr(
        ingest,
        "_load_sync",
        lambda path: [Document(page_content=path.read_text(encoding="utf-8"), metadata={"source": path.name})],
    )
    monkeypatch.setattr(ingest, "_split_sync", lambda documents: documents)
    invalidations = []
    monkeypatch.setattr(cache, "invalidate_user_cache", lambda uid: invalidations.append(("user", uid)))
    monkeypatch.setattr(cache, "invalidate_workspace_cache", lambda wid: invalidations.append(("workspace", wid)))
    return destination, collection, invalidations


def update(destination, content=b"new revision", workspace_id=None):
    return ingest._ingest_file_sync(1, destination, workspace_id=workspace_id, content=content)


@pytest.mark.parametrize("failure", ["load", "embed", "add", "publish", "retire"])
def test_failed_update_preserves_raw_and_entire_previous_index(prepared, monkeypatch, failure):
    destination, collection, invalidations = prepared
    previous = copy.deepcopy(collection.rows)

    def fail(*args, **kwargs):
        raise RuntimeError(f"{failure} failure")

    if failure == "load":
        monkeypatch.setattr(ingest, "_load_sync", fail)
    elif failure == "embed":
        monkeypatch.setattr(ingest, "embed_texts_with_retry", fail)
    elif failure == "add":
        collection.fail_add = True
    elif failure == "publish":
        monkeypatch.setattr(ingest, "publish_raw", fail)
    else:
        collection.fail_retire = True

    with pytest.raises(RuntimeError):
        update(destination)
    assert destination.read_bytes() == b"old revision"
    assert collection.rows == previous
    assert not list(destination.parent.glob(".document-stage-*"))
    assert not list(destination.parent.glob(".document-backup-*"))
    assert bool(invalidations) == (failure in {"add", "publish", "retire"})


def test_success_replaces_legacy_ids_and_keeps_original_source(prepared):
    destination, collection, invalidations = prepared
    workspace_destination = destination.parents[1] / "workspaces" / "12" / destination.name
    workspace_destination.parent.mkdir(parents=True)
    destination.replace(workspace_destination)
    destination = workspace_destination
    assert update(destination, workspace_id=12) == 1
    assert destination.read_bytes() == b"new revision"
    rows = collection.get(where={"source": "manual.txt"})
    assert rows["documents"] == ["new revision"]
    assert all("document_version" in meta for meta in rows["metadatas"])
    assert "legacy-0" not in collection.rows and "legacy-1" not in collection.rows
    assert collection.rows["other"][0] == "unrelated document"
    assert invalidations == [("workspace", 12)]


def test_failed_first_publication_does_not_leave_raw_or_chunks(prepared, monkeypatch):
    destination, collection, _ = prepared
    destination.unlink()
    collection.rows = {"other": collection.rows["other"]}
    monkeypatch.setattr(ingest, "publish_raw", lambda *args: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError):
        update(destination)
    assert not destination.exists()
    assert list(collection.rows) == ["other"]


def test_empty_document_cannot_replace_existing_document(prepared, monkeypatch):
    destination, collection, _ = prepared
    previous = copy.deepcopy(collection.rows)
    monkeypatch.setattr(ingest, "_split_sync", lambda documents: [])
    with pytest.raises(ValueError, match="no readable text"):
        update(destination)
    assert destination.read_bytes() == b"old revision"
    assert collection.rows == previous


def test_concurrent_failed_update_cannot_rollback_another_success(prepared, monkeypatch):
    destination, collection, _ = prepared
    first_preparing = threading.Event()
    release_first = threading.Event()
    second_preparing = threading.Event()
    original_load = ingest._load_sync
    original_publish = ingest.publish_raw

    def load(path):
        if path.read_bytes() == b"first success":
            first_preparing.set()
            assert release_first.wait(5)
        else:
            second_preparing.set()
        return original_load(path)

    def publish(staged, target):
        if staged.read_bytes() == b"second failure":
            raise OSError("second publish failed")
        original_publish(staged, target)

    monkeypatch.setattr(ingest, "_load_sync", load)
    monkeypatch.setattr(ingest, "publish_raw", publish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(update, destination, b"first success")
        assert first_preparing.wait(5)
        second = pool.submit(update, destination, b"second failure")
        try:
            assert not second_preparing.wait(0.1)
        finally:
            release_first.set()
        assert first.result(timeout=5) == 1
        with pytest.raises(OSError):
            second.result(timeout=5)
    assert destination.read_bytes() == b"first success"
    assert collection.get(where={"source": "manual.txt"})["documents"] == ["first success"]


def test_reader_cannot_observe_staged_and_old_chunks_together(prepared, monkeypatch):
    destination, collection, _ = prepared
    added = threading.Event()
    release = threading.Event()
    reader_entered = threading.Event()
    original_add = collection.add

    def add(**kwargs):
        original_add(**kwargs)
        added.set()
        assert release.wait(5)

    def search(*args):
        reader_entered.set()
        data = collection.get(where={"source": "manual.txt"})
        return [(text, "manual.txt", None) for text in data["documents"]]

    monkeypatch.setattr(collection, "add", add)
    monkeypatch.setattr(retriever, "_search_sync", search)
    monkeypatch.setattr(retriever, "_bm25_search_sync", lambda *args: [])
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(update, destination)
        assert added.wait(5)
        reader = pool.submit(lambda: asyncio.run(retriever._hybrid_search(1, "q", 5)))
        try:
            assert not reader_entered.wait(0.1)
        finally:
            release.set()
        assert writer.result(timeout=5) == 1
        assert reader.result(timeout=5) == [("new revision", "manual.txt", None)]


def test_old_redis_entries_cannot_survive_new_document_revision(prepared, monkeypatch):
    destination, collection, _ = prepared
    entries = {}
    monkeypatch.setattr(retriever, "get_cached_chunks", lambda uid, query, *args: entries.get(query))
    monkeypatch.setattr(
        retriever, "set_cached_chunks", lambda uid, query, k, rows, *args: entries.__setitem__(query, rows)
    )
    monkeypatch.setattr(
        retriever,
        "_search_sync",
        lambda *args: [
            (text, "manual.txt", None) for text in collection.get(where={"source": "manual.txt"})["documents"]
        ],
    )
    monkeypatch.setattr(retriever, "_bm25_search_sync", lambda *args: [])
    monkeypatch.setattr(retriever, "rerank_chunks", lambda query, rows, **kw: rows)
    assert asyncio.run(retriever.retrieve(1, "same question"))[0][0] == "old revision"
    assert update(destination) == 1
    # Fixture intentionally leaves old Redis entries in place: revision keys isolate them.
    assert asyncio.run(retriever.retrieve(1, "same question")) == [("new revision", "manual.txt", None)]
    assert len(entries) == 2


def test_stale_rerank_cannot_repopulate_current_cache(prepared, monkeypatch):
    _, _, _ = prepared
    writes = []
    previous = read_revision(ingest.CHROMA_DIR, "user_1")
    with storage_lock(ingest.CHROMA_DIR, "user_1"):
        advance_revision(ingest.CHROMA_DIR, "user_1")
    monkeypatch.setattr(retriever, "set_cached_chunks", lambda *args: writes.append(args))
    retriever._cache_snapshot(1, "q", 5, [], None, None, previous)
    assert writes == []


def test_delete_failure_restores_index_and_keeps_raw(prepared, monkeypatch):
    destination, collection, invalidations = prepared
    previous = copy.deepcopy(collection.rows)
    original_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == destination:
            raise OSError("raw delete failed")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(OSError):
        ingest._delete_doc_sync(1, destination.name, file_path=destination)
    assert destination.read_bytes() == b"old revision"
    assert collection.rows == previous
    assert invalidations == [("user", 1)]


def _lock_process(index_dir, attempting, acquired):
    attempting.set()
    with storage_lock(Path(index_dir), "user_1", shared=True):
        acquired.set()


def test_publication_lock_blocks_a_separate_process(prepared):
    _, _, _ = prepared
    ctx = multiprocessing.get_context("spawn")
    attempting, acquired = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_lock_process, args=(str(ingest.CHROMA_DIR), attempting, acquired))
    with storage_lock(ingest.CHROMA_DIR, "user_1"):
        process.start()
        try:
            # Tiến trình con (spawn) phải import lại chromadb/langchain: 6–9 s trên WSL, lâu hơn khi
            # máy bận. 15 s từng làm test chập chờn; hạn chờ KHỞI ĐỘNG dài hơn không nới điều kiện
            # được kiểm (con không lấy được khoá khi cha đang giữ).
            assert attempting.wait(60)
            assert not acquired.wait(0.1)
        except BaseException:
            process.terminate()
            process.join(5)
            raise
    assert acquired.wait(30)
    process.join(30)
    assert process.exitcode == 0


def test_upload_route_stages_content_before_touching_previous_raw(prepared, monkeypatch):
    import io
    from types import SimpleNamespace

    from fastapi import HTTPException, UploadFile

    import docs.router as router

    destination, collection, _ = prepared
    previous = copy.deepcopy(collection.rows)
    monkeypatch.setattr(router, "_path_in_user_dir", lambda *args: destination)

    def fail_load(path):
        assert path != destination
        assert path.name == destination.name
        assert path.read_bytes() == b"replacement"
        assert destination.read_bytes() == b"old revision"
        raise ValueError("parser rejected replacement")

    monkeypatch.setattr(ingest, "_load_sync", fail_load)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            router.upload_doc(
                file=UploadFile(file=io.BytesIO(b"replacement"), filename=destination.name),
                workspace_id=None,
                user=SimpleNamespace(id=1),
                db=None,
            )
        )
    assert caught.value.status_code == 500
    assert destination.read_bytes() == b"old revision"
    assert collection.rows == previous


@pytest.mark.parametrize("kind", ["list", "suggest"])
def test_document_metadata_and_suggestions_wait_for_publication(prepared, monkeypatch, kind):
    import rag.suggest as suggest

    _, collection, _ = prepared
    entered = threading.Event()
    original_get = collection.get

    def get(*args, **kwargs):
        entered.set()
        return original_get(*args, **kwargs)

    monkeypatch.setattr(collection, "get", get)
    monkeypatch.setattr(suggest, "CHROMA_DIR", ingest.CHROMA_DIR)

    class Store:
        def similarity_search(self, *args, **kwargs):
            entered.set()
            return [Document(page_content="stable revision")]

    monkeypatch.setattr(suggest, "get_vectorstore", lambda *args: Store())
    read = (lambda: ingest._list_docs_sync(1)) if kind == "list" else (lambda: suggest._sample_context_sync(1))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with storage_lock(ingest.CHROMA_DIR, "user_1"):
            result = pool.submit(read)
            assert not entered.wait(0.1)
        assert result.result(timeout=5)
    assert entered.is_set()


def _revision_process(index_dir):
    with storage_lock(Path(index_dir), "user_1"):
        advance_revision(Path(index_dir), "user_1")


def test_external_worker_revision_invalidates_local_bm25(prepared, monkeypatch):
    import rag.bm25 as bm25
    from rag.document_lifecycle import sync_bm25_revision

    _, _, _ = prepared
    invalidated = []
    monkeypatch.setattr(bm25, "invalidate", invalidated.append)
    first = read_revision(ingest.CHROMA_DIR, "user_1")
    sync_bm25_revision(ingest.CHROMA_DIR, "user_1", first)
    process = multiprocessing.get_context("spawn").Process(target=_revision_process, args=(str(ingest.CHROMA_DIR),))
    process.start()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("revision worker did not finish")
    assert process.exitcode == 0
    second = read_revision(ingest.CHROMA_DIR, "user_1")
    assert second != first
    sync_bm25_revision(ingest.CHROMA_DIR, "user_1", second)
    assert invalidated == ["user_1", "user_1"]


@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_postreplace_commit_directory_fsync_error_never_undoes_commit(prepared, monkeypatch, operation):
    import json

    import rag.document_lifecycle as lifecycle

    destination, collection, _ = prepared
    journal = lifecycle._journal_path(ingest.CHROMA_DIR, "user_1")
    original_sync = lifecycle._sync_directory
    injected = []

    def sync(path):
        if not injected and path == journal.parent and journal.exists():
            record = json.loads(journal.read_text(encoding="utf-8"))
            if record["phase"] == "committed":
                injected.append(True)
                raise OSError("directory fsync failed after committed marker replace")
        return original_sync(path)

    monkeypatch.setattr(lifecycle, "_sync_directory", sync)
    monkeypatch.setattr(collection, "upsert", lambda **kw: pytest.fail("Cannot undo a persisted commit"))
    if operation == "replace":
        assert update(destination) == 1
        assert destination.read_bytes() == b"new revision"
        assert collection.get(where={"source": destination.name})["documents"] == ["new revision"]
    else:
        assert ingest._delete_doc_sync(1, destination.name, file_path=destination) == 2
        assert not destination.exists()
        assert collection.get(where={"source": destination.name})["ids"] == []
    assert injected
    record = lifecycle._read_journal(ingest.CHROMA_DIR, "user_1")
    assert record["phase"] == "committed"
    assert (lifecycle._artifact_dir(ingest.CHROMA_DIR, record) / "raw.backup").exists()
    with lifecycle.collection_read_lock(ingest.CHROMA_DIR, "user_1"):
        pass
    assert not journal.exists()


@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_non_oserror_terminal_cleanup_is_deferred_and_does_not_undo_commit(prepared, monkeypatch, operation):
    import rag.document_lifecycle as lifecycle

    destination, collection, _ = prepared
    original_cleanup = lifecycle._cleanup_transaction
    failures = []

    def cleanup(index, record):
        if not failures:
            failures.append(True)
            raise RuntimeError("terminal cleanup failed")
        original_cleanup(index, record)

    monkeypatch.setattr(lifecycle, "_cleanup_transaction", cleanup)
    monkeypatch.setattr(collection, "upsert", lambda **kw: pytest.fail("Cannot undo a persisted commit"))
    if operation == "replace":
        assert update(destination) == 1
        assert destination.read_bytes() == b"new revision"
        assert collection.get(where={"source": destination.name})["documents"] == ["new revision"]
    else:
        assert ingest._delete_doc_sync(1, destination.name, file_path=destination) == 2
        assert not destination.exists()
        assert collection.get(where={"source": destination.name})["ids"] == []
    assert lifecycle._read_journal(ingest.CHROMA_DIR, "user_1")["phase"] == "committed"
    with lifecycle.collection_read_lock(ingest.CHROMA_DIR, "user_1"):
        pass
    assert not lifecycle._journal_path(ingest.CHROMA_DIR, "user_1").exists()


@pytest.mark.parametrize("operation", ["replace", "delete"])
@pytest.mark.parametrize("marker_failure", ["missing", "corrupt"])
def test_unknown_commit_marker_fails_closed_without_blind_rollback(prepared, monkeypatch, operation, marker_failure):
    import rag.document_lifecycle as lifecycle

    destination, collection, _ = prepared
    journal = lifecycle._journal_path(ingest.CHROMA_DIR, "user_1")
    original_write = lifecycle._write_journal

    def write(index, record):
        if record["phase"] == "committed":
            if marker_failure == "missing":
                journal.unlink()
            else:
                journal.write_text("{", encoding="utf-8")
            raise OSError("commit marker cannot be determined")
        return original_write(index, record)

    monkeypatch.setattr(lifecycle, "_write_journal", write)
    monkeypatch.setattr(
        collection, "upsert", lambda **kw: pytest.fail("Cannot guess rollback with an uncertain commit")
    )
    action = (
        (lambda: update(destination))
        if operation == "replace"
        else (lambda: ingest._delete_doc_sync(1, destination.name, file_path=destination))
    )
    with pytest.raises(lifecycle.DocumentCommitUncertainError):
        action()
    if operation == "replace":
        assert destination.read_bytes() == b"new revision"
        assert collection.get(where={"source": destination.name})["documents"] == ["new revision"]
    else:
        assert not destination.exists()
        assert collection.get(where={"source": destination.name})["ids"] == []
    assert journal.exists()
    assert list(journal.parent.glob("transaction-*/raw.backup"))
    with pytest.raises(lifecycle.DocumentRecoveryError):
        with lifecycle.collection_read_lock(ingest.CHROMA_DIR, "user_1"):
            pytest.fail("An uncertain commit must never be served")
    with pytest.raises(lifecycle.DocumentRecoveryError):
        lifecycle.recover_all_pending(ingest.CHROMA_DIR)


@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_commit_marker_failure_before_replace_rolls_back_known_uncommitted_change(prepared, monkeypatch, operation):
    import rag.document_lifecycle as lifecycle

    destination, collection, _ = prepared
    previous = copy.deepcopy(collection.rows)
    original_write = lifecycle._write_journal

    def write(index, record):
        if record["phase"] == "committed":
            raise OSError("commit marker failed before replace")
        original_write(index, record)

    monkeypatch.setattr(lifecycle, "_write_journal", write)
    action = (
        (lambda: update(destination))
        if operation == "replace"
        else (lambda: ingest._delete_doc_sync(1, destination.name, file_path=destination))
    )
    with pytest.raises(OSError, match="before replace"):
        action()
    assert destination.read_bytes() == b"old revision"
    assert collection.rows == previous
    assert not lifecycle._journal_path(ingest.CHROMA_DIR, "user_1").exists()
