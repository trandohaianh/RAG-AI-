"""Document locks and cache revisions shared by API and batch workers.

Use a document lock during preparation, then a collection lock only for publishing.
Readers hold shared collection locks while taking their retrieval snapshot.
"""
from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import logging
import shutil
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from config import UPLOAD_DIR

_SEEN_REVISIONS: dict[tuple[str, str], str] = {}
_REVISION_LOCK = threading.Lock()
_UNCERTAIN_TRANSACTIONS: dict[tuple[str, str], str] = {}


def _lock_path(index_dir: Path, collection: str, source: str | None = None) -> Path:
    lock_dir = index_dir.parent / ".rag-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    identity = f"{collection}:{source}" if source is not None else collection
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return lock_dir / f"{digest}.lock"


@contextmanager
def storage_lock(index_dir: Path, collection: str, *, source: str | None = None, shared=False):
    """Lock separate file handles across threads/processes on Windows and POSIX."""
    path = _lock_path(index_dir, collection, source)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            from ctypes import wintypes

            class Overlapped(ctypes.Structure):
                _fields_ = [
                    ("Internal", ctypes.c_size_t),
                    ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD),
                    ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE),
                ]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            lock = kernel.LockFileEx
            lock.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped),
            ]
            lock.restype = wintypes.BOOL
            unlock = kernel.UnlockFileEx
            unlock.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                wintypes.DWORD, ctypes.POINTER(Overlapped),
            ]
            unlock.restype = wintypes.BOOL
            native = wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno()))
            overlapped = Overlapped()
            if not lock(native, 0 if shared else 2, 0, 1, 0, ctypes.byref(overlapped)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                yield
            finally:
                if not unlock(native, 0, 1, 0, ctypes.byref(overlapped)):
                    raise ctypes.WinError(ctypes.get_last_error())
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_revision(index_dir: Path, collection: str) -> str:
    try:
        return _lock_path(index_dir, collection).with_suffix(".revision").read_text(encoding="ascii")
    except FileNotFoundError:
        return "legacy"


def advance_revision(index_dir: Path, collection: str) -> str:
    """Call under the exclusive collection lock before the first index mutation."""
    path = _lock_path(index_dir, collection).with_suffix(".revision")
    revision = uuid.uuid4().hex
    fd, temporary = tempfile.mkstemp(prefix=".revision-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(revision)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return revision


def sync_bm25_revision(index_dir: Path, collection: str, revision: str) -> None:
    """Notice commits performed by a different API worker or the batch process."""
    key = (str(index_dir.resolve()), collection)
    with _REVISION_LOCK:
        if _SEEN_REVISIONS.get(key) != revision:
            from rag.bm25 import invalidate

            invalidate(collection)
            _SEEN_REVISIONS[key] = revision


def publish_raw(staged: Path, destination: Path) -> None:
    """The staging directory must be on the same filesystem as the destination."""
    with staged.open("r+b") as handle:
        os.fsync(handle.fileno())
    os.replace(staged, destination)
    _sync_directory(destination.parent)


class DocumentRecoveryError(RuntimeError):
    """A pending publication could not be recovered; readers must fail closed."""


class DocumentCommitUncertainError(DocumentRecoveryError):
    """Commit marker cannot be determined; keep undo data and never guess undo."""


def _check_uncertain(index_dir: Path, collection: str) -> None:
    if (str(index_dir.resolve()), collection) in _UNCERTAIN_TRANSACTIONS:
        raise DocumentCommitUncertainError(f"Document commit needs inspection for {collection}")


def _sync_directory(path: Path) -> None:
    # Windows has no portable directory fsync. Flush file contents on both
    # platforms, and directory entries on POSIX where supported.
    if os.name == "nt":
        return
    import errno
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        if error.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EBADF}:
            raise


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".journal-write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _journal_path(index_dir: Path, collection: str) -> Path:
    return _lock_path(index_dir, collection).with_suffix(".journal.json")


def _artifact_dir(index_dir: Path, record: dict) -> Path:
    root = _journal_path(index_dir, record["collection"]).parent.resolve()
    path = root / ("transaction-" + record["transaction"])
    if path.resolve().parent != root or path.is_symlink():
        raise DocumentRecoveryError("Document artifacts point outside the journal directory")
    return path


def _raw_target(record: dict, upload_root: Path | None = None) -> Path | None:
    value = record.get("raw_path")
    if value is None:
        return None
    target = Path(value)
    root = Path(upload_root if upload_root is not None else UPLOAD_DIR).resolve()
    if not target.is_absolute() or target != target.resolve() or not target.is_relative_to(root):
        raise DocumentRecoveryError("Document journal raw path is outside the upload directory")
    if target.name != record["source"] or target == root:
        raise DocumentRecoveryError("Document journal source does not match its raw path")
    scope = re.fullmatch(r"(user|workspace)_([1-9][0-9]*)", record["collection"])
    if scope is None:
        raise DocumentRecoveryError("Unknown document storage scope")
    expected = root / scope[2] if scope[1] == "user" else root / "workspaces" / scope[2]
    if target.parent != expected.resolve():
        raise DocumentRecoveryError("Document journal raw path belongs to another scope")
    return target


def _read_journal(index_dir: Path, collection: str, *, upload_root: Path | None = None) -> dict | None:
    try:
        record = json.loads(_journal_path(index_dir, collection).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as error:
        raise DocumentRecoveryError(f"Unreadable document journal for {collection}") from error
    try:
        digest = record.get("journal_sha256")
        actual = hashlib.sha256(_record_payload(record)).hexdigest()
        if not isinstance(digest, str) or not hmac.compare_digest(digest, actual):
            raise ValueError("Journal checksum differs")
    except Exception as error:
        raise DocumentRecoveryError(f"Invalid document journal checksum for {collection}") from error
    phases = {"prepared", "adding", "publishing", "retiring", "deleting", "committed", "rolling_back", "rolled_back", "commit_uncertain"}
    try:
        valid = (
            record["schema"] == 1 and record["collection"] == collection
            and isinstance(record["source"], str) and bool(record["source"])
            and record["phase"] in phases and record["operation"] in {"replace", "delete"}
            and uuid.UUID(hex=record["transaction"]).hex == record["transaction"]
            and isinstance(record["new_ids"], list)
            and all(isinstance(value, str) for value in record["new_ids"])
            and len(set(record["new_ids"])) == len(record["new_ids"])
            and isinstance(record["raw_existed"], bool)
            and isinstance(record["snapshot_sha256"], str) and len(record["snapshot_sha256"]) == 64
        )
        if not valid:
            raise ValueError("Journal schema is incomplete")
        _raw_target(record, upload_root)
    except Exception as error:
        raise DocumentRecoveryError(f"Invalid document journal for {collection}") from error
    return record


def _record_payload(record: dict) -> bytes:
    clean = {key: value for key, value in record.items() if key != "journal_sha256"}
    return json.dumps(clean, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")


def _write_journal(index_dir: Path, record: dict) -> None:
    clean = {key: value for key, value in record.items() if key != "journal_sha256"}
    clean["journal_sha256"] = hashlib.sha256(_record_payload(record)).hexdigest()
    _atomic_bytes(_journal_path(index_dir, record["collection"]),
                  json.dumps(clean, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8"))


def _snapshot_payload(snapshot: dict) -> bytes:
    # Persist embeddings so recovery never calls the provider, including legacy IDs.
    result = {}
    for key in ("ids", "documents", "metadatas", "embeddings"):
        value = snapshot.get(key)
        if hasattr(value, "tolist"):
            value = value.tolist()
        result[key] = [] if value is None else value
    if any(len(result[key]) != len(result["ids"]) for key in ("documents", "metadatas", "embeddings")):
        raise DocumentRecoveryError("Incomplete document snapshot")
    return json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")


def _copy_durable(source: Path, destination: Path) -> None:
    # Keep the sole backup intact so another interruption can repeat restoration.
    descriptor, temporary = tempfile.mkstemp(prefix=".document-restore-", dir=destination.parent)
    try:
        with source.open("rb") as original, os.fdopen(descriptor, "wb") as copied:
            shutil.copyfileobj(original, copied)
            copied.flush()
            os.fsync(copied.fileno())
        os.replace(temporary, destination)
        _sync_directory(destination.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def begin_transaction(index_dir: Path, collection: str, source: str, previous: dict, *,
                      new_ids: list[str], raw_path: Path | None = None,
                      operation: str = "replace", upload_root: Path | None = None) -> dict:
    """Persist undo data before mutation. Hold document and collection locks."""
    if _journal_path(index_dir, collection).exists():
        raise DocumentRecoveryError("Previous document publication needs recovery")
    record = {"schema": 1, "collection": collection, "source": source,
              "transaction": uuid.uuid4().hex, "phase": "prepared", "operation": operation,
              "new_ids": new_ids, "raw_path": str(raw_path.resolve()) if raw_path is not None else None,
              "raw_existed": raw_path is not None and raw_path.exists()}
    _raw_target(record, upload_root)
    artifacts = _artifact_dir(index_dir, record)
    artifacts.mkdir()
    _sync_directory(artifacts.parent)
    payload = _snapshot_payload(previous)
    _atomic_bytes(artifacts / "snapshot.json", payload)
    record["snapshot_sha256"] = hashlib.sha256(payload).hexdigest()
    if record["raw_existed"]:
        backup = artifacts / "raw.backup"
        with raw_path.open("rb") as original, backup.open("wb") as copied:
            shutil.copyfileobj(original, copied)
            copied.flush()
            os.fsync(copied.fileno())
        _sync_directory(artifacts)
        record["raw_sha256"] = hashlib.sha256(backup.read_bytes()).hexdigest()
    _write_journal(index_dir, record)
    return record


def transaction_phase(index_dir: Path, record: dict, phase: str) -> None:
    record["phase"] = phase
    _write_journal(index_dir, record)


def _cleanup_transaction(index_dir: Path, record: dict) -> None:
    # Only terminal journals may clean up: a crash here must not undo a commit.
    artifacts = _artifact_dir(index_dir, record)
    if artifacts.exists():
        shutil.rmtree(artifacts)
        _sync_directory(artifacts.parent)
    path = _journal_path(index_dir, record["collection"])
    path.unlink(missing_ok=True)
    _sync_directory(path.parent)


def _uncertain_commit(index_dir: Path, record: dict, *, journal_missing: bool) -> None:
    _UNCERTAIN_TRANSACTIONS[(str(index_dir.resolve()), record["collection"])] = record["transaction"]
    if journal_missing:
        # Keep a durable guard when the original marker disappeared. Never replace
        # an unreadable existing journal: preserve it and its undo data for review.
        try:
            _write_journal(index_dir, {**record, "phase": "commit_uncertain"})
        except Exception:
            logging.getLogger(__name__).critical("Cannot persist uncertain document commit guard", exc_info=True)


def commit_transaction(index_dir: Path, record: dict) -> None:
    try:
        transaction_phase(index_dir, record, "committed")
    except Exception as original_error:
        try:
            persisted = _read_journal(index_dir, record["collection"])
        except Exception as inspection_error:
            _uncertain_commit(index_dir, record, journal_missing=False)
            raise DocumentCommitUncertainError(
                f"Cannot determine document commit for {record['collection']}"
            ) from ExceptionGroup("Commit and journal inspection failed", [original_error, inspection_error])
        if persisted is not None and persisted["transaction"] == record["transaction"]:
            if persisted["phase"] == "committed":
                # os.replace may succeed before directory fsync or cleanup raises.
                # The persisted terminal marker is authoritative; retain undo data
                # until a later reader/startup retries cleanup under the same lock.
                logging.getLogger(__name__).warning("Document commit persisted; cleanup deferred", exc_info=True)
                return
            if persisted["phase"] in {"prepared", "adding", "publishing", "retiring", "deleting"}:
                raise
        _uncertain_commit(index_dir, record, journal_missing=persisted is None)
        raise DocumentCommitUncertainError(
            f"Cannot determine document commit for {record['collection']}"
        ) from original_error
    try:
        _cleanup_transaction(index_dir, record)
    except Exception:
        # Cleanup errors must never undo a committed publication or deletion.
        logging.getLogger(__name__).warning("Document commit cleanup deferred", exc_info=True)


def delete_raw(path: Path) -> None:
    path.unlink(missing_ok=True)
    _sync_directory(path.parent)


def _restore_transaction(index_dir: Path, record: dict, collection, *, upload_root: Path | None = None) -> None:
    artifacts = _artifact_dir(index_dir, record)
    try:
        payload = (artifacts / "snapshot.json").read_bytes()
        if hashlib.sha256(payload).hexdigest() != record["snapshot_sha256"]:
            raise ValueError("Snapshot checksum differs")
        snapshot = json.loads(payload)
        if _snapshot_payload(snapshot) != payload:
            raise ValueError("Snapshot contents are incomplete")
        if set(snapshot["ids"]) & set(record["new_ids"]):
            raise ValueError("Old and new document IDs overlap")
        if any(not meta or meta.get("source") != record["source"] for meta in snapshot["metadatas"]):
            raise ValueError("Snapshot contains another document")
        target = _raw_target(record, upload_root)
        if target is not None and record["raw_existed"]:
            backup = artifacts / "raw.backup"
            if hashlib.sha256(backup.read_bytes()).hexdigest() != record.get("raw_sha256"):
                raise ValueError("Raw backup checksum differs")
    except Exception as error:
        raise DocumentRecoveryError(f"Invalid undo snapshot for {record['collection']}") from error
    transaction_phase(index_dir, record, "rolling_back")
    advance_revision(index_dir, record["collection"])
    if snapshot["ids"]:
        collection.upsert(**snapshot)
    if record["new_ids"]:
        collection.delete(ids=record["new_ids"])
    if target is not None:
        if record["raw_existed"]:
            _copy_durable(backup, target)
        else:
            target.unlink(missing_ok=True)
            _sync_directory(target.parent)
    transaction_phase(index_dir, record, "rolled_back")
    _cleanup_transaction(index_dir, record)


def rollback_transaction(index_dir: Path, record: dict, collection) -> None:
    """Idempotent undo while holding exclusive publication/document locks."""
    try:
        _restore_transaction(index_dir, record, collection)
    except Exception as error:
        raise DocumentRecoveryError(f"Document rollback needs recovery for {record['collection']}") from error


def _recover_locked(index_dir: Path, record: dict, collection=None, *, upload_root: Path | None = None) -> None:
    try:
        if record["phase"] == "commit_uncertain":
            raise DocumentCommitUncertainError("Document commit marker needs manual inspection")
        if record["phase"] in {"committed", "rolled_back"}:
            _cleanup_transaction(index_dir, record)
            return
        if collection is None:
            import chromadb
            collection = chromadb.PersistentClient(path=str(index_dir)).get_or_create_collection(name=record["collection"])
        _restore_transaction(index_dir, record, collection, upload_root=upload_root)
    except Exception as error:
        raise DocumentRecoveryError(f"Document recovery failed for {record['collection']}") from error


def recover_pending(index_dir: Path, collection: str, *, upload_root: Path | None = None) -> bool:
    """Recover with document-before-collection lock order across processes."""
    while True:
        _check_uncertain(index_dir, collection)
        record = _read_journal(index_dir, collection, upload_root=upload_root)
        if record is None:
            return False
        with storage_lock(index_dir, collection, source=record["source"]):
            with storage_lock(index_dir, collection):
                current = _read_journal(index_dir, collection, upload_root=upload_root)
                if current is None:
                    return False
                if current["source"] != record["source"]:
                    continue
                _recover_locked(index_dir, current, upload_root=upload_root)
                return True


@contextmanager
def publication_lock(index_dir: Path, collection: str, *, source_locked: str | None = None):
    """Caller may already hold the named document lock during preparation."""
    while True:
        with storage_lock(index_dir, collection):
            _check_uncertain(index_dir, collection)
            record = _read_journal(index_dir, collection)
            if record is None:
                yield
                return
            if source_locked == record["source"]:
                _recover_locked(index_dir, record)
                yield
                return
        # Release collection before acquiring another document's lock.
        recover_pending(index_dir, collection)


@contextmanager
def collection_read_lock(index_dir: Path, collection: str, *, upload_root: Path | None = None):
    """Recover before exposing snapshots/cache keys; fail closed on errors."""
    while True:
        with storage_lock(index_dir, collection, shared=True):
            _check_uncertain(index_dir, collection)
            if not _journal_path(index_dir, collection).exists():
                yield
                return
        recover_pending(index_dir, collection, upload_root=upload_root)


def recover_all_pending(index_dir: Path, *, upload_root: Path | None = None) -> int:
    """Unresolved or corrupt journals prevent startup serving mixed revisions."""
    directory = index_dir.parent / ".rag-locks"
    if not directory.exists():
        return 0
    recovered = 0
    for path in directory.glob("*.journal.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            collection = record["collection"]
            if not isinstance(collection, str) or _journal_path(index_dir, collection) != path:
                raise ValueError("Journal filename does not match collection")
        except FileNotFoundError:
            continue
        except Exception as error:
            raise DocumentRecoveryError(f"Invalid document journal: {path.name}") from error
        recovered += int(recover_pending(index_dir, collection, upload_root=upload_root))
    return recovered
