"""Bước 8: sao lưu rồi khôi phục được (pipeline.jobs.tasks.backup + pipeline.jobs.restore)."""

from __future__ import annotations

import io
import sqlite3
import tarfile
import time
from pathlib import Path

import pytest

from pipeline.jobs import restore as restore_mod
from pipeline.jobs.tasks import backup


@pytest.fixture
def data(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    db = root / "app.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        conn.execute("CREATE TABLE field_service_cases (id INTEGER PRIMARY KEY, title TEXT)")
        conn.executemany("INSERT INTO users (email) VALUES (?)", [("a@example.com",), ("b@example.com",)])
        conn.execute("INSERT INTO field_service_cases (title) VALUES ('Mất mạng')")
    photos = root / "field_photos" / "ws1"
    photos.mkdir(parents=True)
    (photos / "p1.jpg").write_bytes(b"jpeg-1")
    monkeypatch.setattr("config.DATA_ROOT", root)
    monkeypatch.setattr("config.DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr("config.CHROMA_DIR", root / "chroma_db")
    monkeypatch.setattr("config.UPLOAD_DIR", root / "uploads")
    monkeypatch.setattr("config.FIELD_PHOTO_DIR", root / "field_photos")
    monkeypatch.delenv("BACKUP_DIR", raising=False)
    return root, db


def _users(db):
    with sqlite3.connect(db) as conn:
        return [r[0] for r in conn.execute("SELECT email FROM users ORDER BY id")]


def test_backup_then_restore_brings_back_db_and_photos(data):
    root, db = data
    made, detail, status = backup(None)
    assert status == "ok" and made == 2  # app-*.db + photos-*.tar.gz
    # Sự cố: mất một người dùng, ảnh bị ghi đè, có ảnh lạ.
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM users WHERE email = 'b@example.com'")
    (root / "field_photos" / "ws1" / "p1.jpg").write_bytes(b"broken")
    (root / "field_photos" / "ws1" / "junk.jpg").write_bytes(b"junk")

    [stamp] = restore_mod.list_sets(root / "backups")
    checked = restore_mod.restore(stamp, dry_run=True)
    assert checked["db"] == {"users": 2, "cases": 1} and checked["archives"] == {"photos": 1}
    assert _users(db) == ["a@example.com"]  # dry-run không đổi gì

    report = restore_mod.restore(stamp)
    assert _users(db) == ["a@example.com", "b@example.com"]
    assert (root / "field_photos" / "ws1" / "p1.jpg").read_bytes() == b"jpeg-1"
    assert not (root / "field_photos" / "ws1" / "junk.jpg").exists()
    aside = Path(report["previous_data_moved_to"])
    assert (aside / "app.db").exists() and (aside / "field_photos" / "ws1" / "junk.jpg").exists()  # không xoá


def test_latest_set_is_chosen_and_yes_is_required(data, capsys):
    root, db = data
    backup(None)
    time.sleep(0.01)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO users (email) VALUES ('c@example.com')")
    backup(None)
    assert len(restore_mod.list_sets(root / "backups")) == 2
    assert restore_mod.main(["--latest"]) == 2 and "--yes" in capsys.readouterr().err
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM users")
    assert restore_mod.main(["--latest", "--yes"]) == 0
    assert _users(db) == ["a@example.com", "b@example.com", "c@example.com"]


def test_corrupt_database_is_refused_before_touching_data(data):
    root, db = data
    backup(None)
    [stamp] = restore_mod.list_sets(root / "backups")
    (root / "backups" / f"app-{stamp}.db").write_bytes(b"not sqlite at all" * 100)
    with pytest.raises(restore_mod.RestoreError, match="SQLite"):
        restore_mod.restore(stamp)
    assert _users(db) == ["a@example.com", "b@example.com"]
    assert not any(p.name.startswith("pre-restore-") for p in root.iterdir())


def test_archive_with_path_traversal_is_refused(data):
    root, db = data
    backup(None)
    [stamp] = restore_mod.list_sets(root / "backups")
    evil = root / "backups" / f"photos-{stamp}.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo("field_photos/../../escape.txt")
        payload = b"x"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(restore_mod.RestoreError, match="không an toàn"):
        restore_mod.restore(stamp)
    assert not (root.parent / "escape.txt").exists()
