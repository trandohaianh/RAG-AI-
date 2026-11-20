"""Integration-style tests — file discovery (không cần Postgres)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from pipeline.medallion import discover_upload_files


def test_discover_upload_files_empty(tmp_path):
    assert discover_upload_files(tmp_path) == []


def test_discover_upload_files_finds_txt(tmp_path):
    user_dir = tmp_path / "1"
    user_dir.mkdir()
    (user_dir / "note.txt").write_text("hello", encoding="utf-8")
    found = discover_upload_files(tmp_path)
    assert len(found) == 1
    assert found[0][0] == 1
    assert found[0][1].name == "note.txt"
