"""Great Expectations–style validation trước khi ghi Silver layer."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pandas as pd

from config import CHUNK_SIZE


@dataclass
class ValidationResult:
    success: bool
    expectation_results: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "expectation_results": self.expectation_results,
        }


def _chunk_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def chunks_to_dataframe(chunks: list[dict]) -> pd.DataFrame:
    rows = []
    for i, c in enumerate(chunks):
        content = (c.get("content") or "").strip()
        rows.append(
            {
                "chunk_index": i,
                "content": content,
                "content_hash": _chunk_hash(content),
                "char_count": len(content),
                "source": c.get("source", ""),
            }
        )
    return pd.DataFrame(rows)


MIN_CHUNK_CHARS = 10


def drop_noise_chunks(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Bỏ chunk rác thay vì đánh hỏng cả tài liệu.

    Chân trang "Trang 3" (< 10 ký tự) hay một đoạn lặp lại (tiêu đề đầu trang, khối miễn trừ) có
    mặt ở gần như mọi PDF thật; trước đây chỉ một chunk như vậy làm cả file trượt kiểm tra và
    không vào được Silver. Chunk rỗng/quá dài vẫn bị kiểm tra như cũ.
    """
    if df.empty:
        return df, []
    content = df["content"].fillna("")
    tiny = (content.str.len() > 0) & (content.str.len() < MIN_CHUNK_CHARS)
    dupes = df["content_hash"].duplicated() & ~tiny
    notes = []
    if tiny.any():
        notes.append({"expectation": "drop_tiny_chunks", "success": True,
                      "detail": f"Đã bỏ {int(tiny.sum())} chunk dưới {MIN_CHUNK_CHARS} ký tự (chân/đầu trang)"})
    if dupes.any():
        notes.append({"expectation": "drop_duplicate_chunks", "success": True,
                      "detail": f"Đã bỏ {int(dupes.sum())} chunk trùng nội dung"})
    cleaned = df[~(tiny | dupes)].reset_index(drop=True)
    return cleaned, notes


def validate_document_chunks(df: pd.DataFrame) -> ValidationResult:
    """
    Checkpoint tương đương GE suite `document_chunks`:
    - content không null/empty
    - không trùng content_hash
    - char_count trong khoảng hợp lệ
    - source không null
    """
    results: list[dict] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        results.append({"expectation": name, "success": passed, "detail": detail})

    if df.empty:
        check("expect_table_row_count_to_be_between", False, "Không có chunk")
        return ValidationResult(success=False, expectation_results=results)

    check(
        "expect_column_values_to_not_be_null",
        not df["content"].isna().any() and (df["content"].str.len() > 0).all(),
        "content null hoặc rỗng",
    )
    check(
        "expect_column_values_to_not_be_null",
        not df["source"].isna().any() and (df["source"].str.len() > 0).all(),
        "source null",
    )
    dupes = df["content_hash"].duplicated().sum()
    check(
        "expect_column_values_to_be_unique",
        dupes == 0,
        f"{dupes} chunk trùng content_hash",
    )
    max_chars = int(CHUNK_SIZE * 1.5)
    bad_len = ((df["char_count"] < 10) | (df["char_count"] > max_chars)).sum()
    check(
        "expect_column_values_to_be_between",
        bad_len == 0,
        f"{bad_len} chunk ngoài khoảng 10–{max_chars} ký tự",
    )

    success = all(r["success"] for r in results)
    return ValidationResult(success=success, expectation_results=results)


def validate_chunks_list(chunks: list[dict]) -> ValidationResult:
    return validate_document_chunks(chunks_to_dataframe(chunks))
