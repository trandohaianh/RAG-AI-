"""Real process termination with a durable SQLite fake for the Chroma contract.

No embedding provider or LLM is contacted. SQLite commits each fake collection
mutation, so killing the worker cannot hide an interrupted add/delete/rollback
behind an in-memory test double.
"""
from __future__ import annotations

import json
import multiprocessing
import sqlite3
from pathlib import Path

import pytest
from langchain_core.documents import Document

import rag.document_lifecycle as lifecycle
import rag.ingest as ingest


OLD_ROWS = {
    "legacy-0": ("old revision", {"source": "manual.txt"}, [1.0, 0.0]),
    "legacy-1": ("old extra chunk", {"source": "manual.txt"}, [0.5, 0.5]),
    "other": ("unrelated document", {"source": "other.txt"}, [0.0, 1.0]),
}


class DurableCollection:
    def __init__(self, path, pause=None):
        self.path = str(path)
        self.pause = pause or (lambda *args: None)
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS rows (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    @property
    def rows(self):
        with sqlite3.connect(self.path) as connection:
            return {key: json.loads(payload) for key, payload in connection.execute("SELECT id,payload FROM rows")}

    def get(self, where=None, include=None):
        rows = [(key, row) for key, row in self.rows.items() if not where or row[1]["source"] == where["source"]]
        return {"ids": [key for key, row in rows], "documents": [row[0] for key, row in rows],
                "metadatas": [row[1] for key, row in rows], "embeddings": [row[2] for key, row in rows]}

    def add(self, ids, documents, metadatas, embeddings):
        for key, text, meta, vector in zip(ids, documents, metadatas, embeddings):
            with sqlite3.connect(self.path) as connection:
                connection.execute("INSERT INTO rows VALUES (?,?)", (key, json.dumps([text, meta, vector])))
            self.pause("added", key)

    def upsert(self, ids, documents, metadatas, embeddings):
        for key, text, meta, vector in zip(ids, documents, metadatas, embeddings):
            with sqlite3.connect(self.path) as connection:
                connection.execute("INSERT OR REPLACE INTO rows VALUES (?,?)", (key, json.dumps([text, meta, vector])))
            self.pause("restored", key)

    def delete(self, ids):
        for key in ids:
            with sqlite3.connect(self.path) as connection:
                connection.execute("DELETE FROM rows WHERE id=?", (key,))
            self.pause("deleted", key)


class DurableClient:
    def __init__(self, collection):
        self.collection = collection

    def get_collection(self, name):
        return self.collection

    def get_or_create_collection(self, name):
        return self.collection


def _writer(index_dir, upload_root, database, target, mode, reached):
    index_dir, upload_root, target = Path(index_dir), Path(upload_root), Path(target)
    lifecycle.UPLOAD_DIR = upload_root
    ingest.CHROMA_DIR = index_dir

    def halt():
        reached.set()
        # Parent terminates this process while it still holds real file locks.
        multiprocessing.Event().wait(120)
        raise RuntimeError("Parent did not terminate worker")

    def pause(event, key):
        if mode == "partial_add" and event == "added":
            halt()
        if mode in {"partial_retire", "partial_delete"} and event == "deleted" and key.startswith("legacy"):
            halt()
        if mode == "rollback_index" and event == "restored":
            halt()
        if mode == "rollback_raw" and event == "deleted" and key.startswith("legacy"):
            raise RuntimeError("Start rollback after partial retirement")

    collection = DurableCollection(database, pause)
    ingest.chromadb.PersistentClient = lambda **kwargs: DurableClient(collection)
    ingest._embeddings = lambda: object()
    ingest.embed_texts_with_retry = lambda model, texts: [[1.0, 1.0] for text in texts]
    ingest._load_sync = lambda path: [Document(page_content=path.read_text(encoding="utf-8"))]
    ingest._split_sync = lambda docs: [Document(page_content="new first"), Document(page_content="new second")]
    ingest._invalidate_scope = lambda *args: None
    import pipeline.db
    pipeline.db.is_medallion_enabled = lambda: False

    original_publish = ingest.publish_raw
    def publish(staged, destination):
        if mode in {"rollback_index", "rolled_back_cleanup"}:
            raise RuntimeError("Start rollback before publication")
        original_publish(staged, destination)
        if mode == "published":
            halt()
    ingest.publish_raw = publish

    original_delete = ingest.delete_raw
    def delete(path):
        original_delete(path)
        if mode == "deleted_raw":
            halt()
    ingest.delete_raw = delete

    original_cleanup = lifecycle._cleanup_transaction
    def cleanup(index, record):
        if mode in {"committed_cleanup", "delete_committed_cleanup"} and record["phase"] == "committed":
            halt()
        if mode == "rolled_back_cleanup" and record["phase"] == "rolled_back":
            halt()
        return original_cleanup(index, record)
    lifecycle._cleanup_transaction = cleanup

    original_copy = lifecycle._copy_durable
    def copy(source, destination):
        original_copy(source, destination)
        if mode == "rollback_raw":
            halt()
    lifecycle._copy_durable = copy

    original_write = lifecycle._write_journal
    def write(index, record):
        original_write(index, record)
        if mode == "prepared" and record["phase"] == "prepared":
            halt()
    lifecycle._write_journal = write

    if mode in {"partial_delete", "deleted_raw", "delete_committed_cleanup"}:
        ingest._delete_doc_sync(1, "manual.txt", file_path=target)
    else:
        ingest._ingest_file_sync(1, target, content=b"new raw revision")


@pytest.fixture
def storage(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    target = upload_root / "1" / "manual.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old revision")
    index_dir = tmp_path / "chroma"
    index_dir.mkdir()
    database = index_dir / "fake.sqlite"
    collection = DurableCollection(database)
    for key, (text, meta, vector) in OLD_ROWS.items():
        collection.upsert(ids=[key], documents=[text], metadatas=[meta], embeddings=[vector])
    monkeypatch.setattr(lifecycle, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(ingest, "CHROMA_DIR", index_dir)
    monkeypatch.setattr(ingest.chromadb, "PersistentClient", lambda **kwargs: DurableClient(collection))
    return index_dir, upload_root, database, target, collection


def terminate_writer(storage, mode):
    index, uploads, database, target, collection = storage
    context = multiprocessing.get_context("spawn")
    reached = context.Event()
    worker = context.Process(target=_writer, args=(str(index), str(uploads), str(database), str(target), mode, reached))
    worker.start()
    try:
        assert reached.wait(25), f"Worker did not reach {mode}; exit code {worker.exitcode}"
    finally:
        if worker.is_alive():
            worker.terminate()
        worker.join(10)
    assert not worker.is_alive()
    assert worker.exitcode != 0
    assert lifecycle._journal_path(index, "user_1").exists()


@pytest.mark.parametrize("mode", [
    "prepared", "partial_add", "published", "partial_retire", "partial_delete", "deleted_raw",
    "rollback_index", "rollback_raw", "rolled_back_cleanup", "committed_cleanup", "delete_committed_cleanup",
])
def test_terminated_publication_and_rollback_recover_before_read(storage, mode):
    index, uploads, database, target, collection = storage
    terminate_writer(storage, mode)
    before_revision = lifecycle.read_revision(index, "user_1")
    with lifecycle.collection_read_lock(index, "user_1"):
        rows = collection.rows
        if mode == "delete_committed_cleanup":
            assert not target.exists()
            assert list(rows) == ["other"]
        elif mode == "committed_cleanup":
            assert target.read_bytes() == b"new raw revision"
            assert collection.get(where={"source": "manual.txt"})["documents"] == ["new first", "new second"]
        else:
            assert target.read_bytes() == b"old revision"
            assert rows == {key: list(value) for key, value in OLD_ROWS.items()}
    assert not lifecycle._journal_path(index, "user_1").exists()
    assert not list((index.parent / ".rag-locks").glob("transaction-*"))
    assert lifecycle.recover_all_pending(index, upload_root=uploads) == 0
    if mode not in {"committed_cleanup", "delete_committed_cleanup", "rolled_back_cleanup"}:
        assert lifecycle.read_revision(index, "user_1") != before_revision


@pytest.mark.parametrize("mode", ["partial_add", "published"])
def test_interrupted_first_publication_leaves_no_new_raw_or_chunks(storage, mode):
    index, uploads, database, target, collection = storage
    target.unlink()
    collection.delete(ids=["legacy-0", "legacy-1"])
    terminate_writer(storage, mode)
    assert lifecycle.recover_all_pending(index, upload_root=uploads) == 1
    assert not target.exists()
    assert list(collection.rows) == ["other"]


def test_corrupt_snapshot_blocks_read_and_preserves_backups(storage):
    index, uploads, database, target, collection = storage
    terminate_writer(storage, "published")
    journal = lifecycle._read_journal(index, "user_1")
    artifacts = lifecycle._artifact_dir(index, journal)
    (artifacts / "snapshot.json").write_bytes(b"damaged")
    before = collection.rows
    with pytest.raises(lifecycle.DocumentRecoveryError):
        with lifecycle.collection_read_lock(index, "user_1"):
            pytest.fail("Reader must not see a mixed index")
    assert collection.rows == before
    assert (artifacts / "raw.backup").read_bytes() == b"old revision"
    assert lifecycle._journal_path(index, "user_1").exists()


@pytest.mark.parametrize("malformation", ["json", "outside", "other_scope"])
def test_invalid_journal_prevents_startup_and_cannot_touch_another_path(storage, malformation):
    index, uploads, database, target, collection = storage
    terminate_writer(storage, "partial_add")
    path = lifecycle._journal_path(index, "user_1")
    before = collection.rows
    if malformation == "json":
        path.write_text("{", encoding="utf-8")
    else:
        record = json.loads(path.read_text(encoding="utf-8"))
        other = index.parent / "unrelated" / "manual.txt" if malformation == "outside" else uploads / "2" / "manual.txt"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"must survive")
        record["raw_path"] = str(other.resolve())
        lifecycle._write_journal(index, record)
    with pytest.raises(lifecycle.DocumentRecoveryError):
        lifecycle.recover_all_pending(index, upload_root=uploads)
    assert collection.rows == before
    if malformation != "json":
        assert other.read_bytes() == b"must survive"
    assert path.exists()


@pytest.mark.parametrize("field", ["phase", "new_ids"])
def test_valid_json_journal_corruption_blocks_recovery(storage, field):
    index, uploads, database, target, collection = storage
    terminate_writer(storage, "partial_add")
    path = lifecycle._journal_path(index, "user_1")
    record = json.loads(path.read_text(encoding="utf-8"))
    artifacts = lifecycle._artifact_dir(index, record)
    record[field] = "committed" if field == "phase" else ["wrong-chunk"]
    path.write_text(json.dumps(record), encoding="utf-8")
    before = collection.rows
    with pytest.raises(lifecycle.DocumentRecoveryError, match="checksum"):
        lifecycle.recover_all_pending(index, upload_root=uploads)
    assert collection.rows == before
    assert target.read_bytes() == b"old revision"
    assert (artifacts / "raw.backup").read_bytes() == b"old revision"
    assert path.exists()


def _real_chroma_writer(index_dir, uploads, target, reached):
    import chromadb
    import pipeline.db

    index_dir, target = Path(index_dir), Path(target)
    lifecycle.UPLOAD_DIR = Path(uploads)
    ingest.CHROMA_DIR = index_dir
    collection = chromadb.PersistentClient(path=str(index_dir)).get_or_create_collection(name="user_1")
    for key, (text, meta, vector) in OLD_ROWS.items():
        collection.add(ids=[key], documents=[text], metadatas=[meta], embeddings=[vector])
    ingest._embeddings = lambda: object()
    ingest.embed_texts_with_retry = lambda model, texts: [[1.0, 1.0] for text in texts]
    ingest._load_sync = lambda path: [Document(page_content=path.read_text(encoding="utf-8"))]
    ingest._split_sync = lambda docs: docs
    ingest._invalidate_scope = lambda *args: None
    pipeline.db.is_medallion_enabled = lambda: False
    original_publish = ingest.publish_raw

    def publish(staged, destination):
        original_publish(staged, destination)
        reached.set()
        multiprocessing.Event().wait(120)
        raise RuntimeError("Parent did not terminate worker")
    ingest.publish_raw = publish
    ingest._ingest_file_sync(1, target, content=b"real Chroma new revision")


def test_real_chroma_recovers_persisted_numpy_snapshot_after_process_termination(tmp_path, monkeypatch):
    import chromadb

    uploads = tmp_path / "uploads"
    target = uploads / "1" / "manual.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old revision")
    index = tmp_path / "real-chroma"
    monkeypatch.setattr(lifecycle, "UPLOAD_DIR", uploads)
    context = multiprocessing.get_context("spawn")
    reached = context.Event()
    worker = context.Process(target=_real_chroma_writer, args=(str(index), str(uploads), str(target), reached))
    worker.start()
    try:
        assert reached.wait(30), f"Real Chroma worker did not publish; exit code {worker.exitcode}"
    finally:
        if worker.is_alive():
            worker.terminate()
        worker.join(10)
    assert worker.exitcode != 0
    assert target.read_bytes() == b"real Chroma new revision"
    with lifecycle.collection_read_lock(index, "user_1"):
        collection = chromadb.PersistentClient(path=str(index)).get_collection(name="user_1")
        result = collection.get(include=["documents", "metadatas", "embeddings"])
        restored = {key: (text, meta, vector.tolist()) for key, text, meta, vector in zip(
            result["ids"], result["documents"], result["metadatas"], result["embeddings"])}
        assert restored == OLD_ROWS
        assert target.read_bytes() == b"old revision"
    assert not lifecycle._journal_path(index, "user_1").exists()
