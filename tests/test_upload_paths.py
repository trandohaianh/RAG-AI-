"""Tên file upload không được thoát ra khỏi thư mục của người dùng."""
import pytest
from fastapi import HTTPException

import docs.router as r


@pytest.fixture(autouse=True)
def tmp_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(r, "UPLOAD_DIR", tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("bao-cao.pdf", "bao-cao.pdf"),
        ("../2/x.txt", "x.txt"),
        ("..\\..\\windows\\evil.txt", "evil.txt"),
        ("/etc/passwd.txt", "passwd.txt"),
        ("C:\\Users\\a\\Điểm thi.docx", "Điểm thi.docx"),
    ],
)
def test_safe_filename_strips_directories(raw, expected):
    assert r.safe_filename(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "..", ".", "../", "a\x00b.txt", "x" * 201])
def test_safe_filename_rejects_bad_names(raw):
    with pytest.raises(HTTPException) as e:
        r.safe_filename(raw)
    assert e.value.status_code == 400


def test_destination_stays_in_user_folder(tmp_upload):
    dest = r._path_in_user_dir(1, r.safe_filename("../2/x.txt"))
    assert dest.parent == (tmp_upload / "1").resolve()
    assert not (tmp_upload / "2").exists()
