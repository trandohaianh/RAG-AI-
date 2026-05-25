"""Unit tests — Great Expectations–style validation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from validation.ge_validate import validate_chunks_list


def test_validate_chunks_ok():
    chunks = [
        {"content": "Đoạn văn đủ dài để qua validation minimum length.", "source": "a.pdf"},
        {"content": "Đoạn thứ hai cũng đủ dài cho test case này.", "source": "a.pdf"},
    ]
    result = validate_chunks_list(chunks)
    assert result.success is True


def test_validate_chunks_duplicate_fails():
    text = "Nội dung trùng lặp đủ dài cho validation."
    chunks = [
        {"content": text, "source": "b.pdf"},
        {"content": text, "source": "b.pdf"},
    ]
    result = validate_chunks_list(chunks)
    assert result.success is False


def test_validate_empty_fails():
    result = validate_chunks_list([])
    assert result.success is False
