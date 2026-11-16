"""Exercise the actual PDF parser consumed by ingestion without external APIs."""
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from pypdf.errors import PdfReadError

from rag.ingest import _load_sync


def make_pdf(path: Path, password: str | None = None) -> None:
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for text in ("FieldOps page one", "FieldOps page two"):
        page = writer.add_blank_page(width=300, height=200)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    with path.open("wb") as output:
        writer.write(output)


def test_real_pdf_preserves_both_pages_and_source_metadata(tmp_path):
    path = tmp_path / "manual.pdf"
    make_pdf(path)
    pages = _load_sync(path)
    assert len(pages) == 2
    assert [page.page_content.strip() for page in pages] == ["FieldOps page one", "FieldOps page two"]
    assert [page.metadata["page"] for page in pages] == [0, 1]
    assert all(Path(page.metadata["source"]).name == "manual.pdf" for page in pages)


def test_password_protected_pdf_is_rejected_before_indexing(tmp_path):
    path = tmp_path / "protected.pdf"
    make_pdf(path, "not-an-application-secret")
    with pytest.raises(PdfReadError):
        _load_sync(path)


def test_corrupt_pdf_is_rejected_before_indexing(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7\ncorrupt document without xref")
    with pytest.raises(PdfReadError):
        _load_sync(path)
