"""Optional local PDF/DOCX layout extraction. Every output starts UNREVIEWED.

OCR, numbering, figures, page spans and section boundaries must be compared with
original pages before approve(). A parser success is never an approval.
"""

from __future__ import annotations

import html
import importlib.metadata
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

from .models import Approval, Block, LayoutBundle, Scope, TechnicalRAGError, digest


class _Table(HTMLParser):
    ALLOWED = {"table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td", "p", "br"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.rows, self.headers = [], [], []
        self.start, self.is_header, self.has_td = None, False, False
        self.cell_count, self.widths = 0, []
        self.stack = []

    @property
    def output_offset(self):
        return sum(map(len, self.parts))

    def handle_starttag(self, tag, attrs):
        if tag not in self.ALLOWED:
            raise TechnicalRAGError("Unsupported table markup; manual transcription required")
        if tag == "tr":
            if self.start is not None:
                raise TechnicalRAGError("Nested table row")
            self.start, self.is_header, self.has_td = self.output_offset, False, False
            self.cell_count = 0
        if tag in {"td", "th"}:
            if self.start is None:
                raise TechnicalRAGError("Table cell outside a row")
            self.cell_count += 1
        if tag == "th":
            self.is_header = True
        if tag == "td":
            self.has_td = True
        attributes = ""
        for key, value in attrs:
            if key in {"rowspan", "colspan"}:
                if value != "1":
                    raise TechnicalRAGError("Merged cells require reviewed transcription")
                # Span of one has no structural effect.
        self.parts.append("<" + tag + attributes + ">")
        if tag != "br":
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag != "br":
            raise TechnicalRAGError("Unexpected empty table element")
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "br":
            return
        if not self.stack or self.stack.pop() != tag:
            raise TechnicalRAGError("Malformed table markup")
        self.parts.append("</" + tag + ">")
        if tag == "tr":
            self.rows.append([self.start, self.output_offset])
            self.headers.append(self.is_header and not self.has_td)
            self.widths.append(self.cell_count)
            self.start = None

    def handle_data(self, data):
        self.parts.append(html.escape(data, quote=False))


def table_layout(raw_html: str) -> tuple[str, list[list[int]], int]:
    parser = _Table()
    parser.feed(raw_html)
    parser.close()
    if parser.stack or not parser.rows or len(set(parser.widths)) != 1 or not parser.widths[0]:
        raise TechnicalRAGError("Incomplete table markup")
    headers = 0
    for flag in parser.headers:
        if not flag:
            break
        headers += 1
    return "".join(parser.parts), parser.rows, headers


def extract_layout(path: Path, scope: Scope, source_id: str) -> LayoutBundle:
    path = Path(path)
    suffix = path.suffix.lower()
    raw_hash = digest(path.read_bytes())
    if suffix == ".pdf":
        from unstructured.partition.pdf import partition_pdf

        elements = partition_pdf(
            filename=str(path),
            strategy="hi_res",
            infer_table_structure=True,
            extract_image_block_types=["Image"],
            extract_image_block_to_payload=True,
        )
    elif suffix == ".docx":
        from unstructured.partition.docx import partition_docx

        elements = partition_docx(filename=str(path), infer_table_structure=True)
    else:
        raise ValueError("Only PDF/DOCX; use reviewed LayoutBundle for other sources")
    blocks, section = [], "section-0"
    for number, element in enumerate(elements):
        category = element.category
        if category == "Title":
            section = "section-" + str(number)
        # DOCX page numbers are layout dependent, never pretend they are PDF pages.
        page = getattr(element.metadata, "page_number", None) if suffix == ".pdf" else None
        pages = [page] if type(page) is int and page > 0 else []
        text = str(element)
        if category == "Table":
            table = getattr(element.metadata, "text_as_html", None)
            if not table:
                raise TechnicalRAGError("Table lost its structure; manual transcription required")
            text, spans, header_count = table_layout(table)
            blocks.append(
                Block(kind="table", text=text, section=section, pages=pages, row_spans=spans, header_rows=header_count)
            )
        elif category in {"Image", "Formula"}:
            blocks.append(
                Block(kind="diagram", text=text or "[UNREVIEWED FIGURE/FORMULA]", section=section, pages=pages)
            )
        elif text:
            blocks.append(
                Block(kind="title" if category == "Title" else "text", text=text, section=section, pages=pages)
            )
    return LayoutBundle(
        source_id=source_id,
        source_name=path.name,
        raw_sha256=raw_hash,
        parser="unstructured:" + importlib.metadata.version("unstructured"),
        scope=scope,
        blocks=blocks,
    )


def approve(bundle: LayoutBundle, raw_path: Path, reviewer: str) -> LayoutBundle:
    """Call only AFTER manual corrections/review; this does not perform the review.

    A local operator assertion, not authentication, authorization or digital signing.
    """
    if digest(Path(raw_path).read_bytes()) != bundle.raw_sha256:
        raise TechnicalRAGError("Raw source changed since extraction")
    if not reviewer.strip():
        raise ValueError("Reviewer identity required")
    approval = Approval(
        reviewer=reviewer,
        reviewed_at=datetime.now(UTC).isoformat(),
        extraction_sha256=bundle.extraction_hash(),
        raw_sha256=bundle.raw_sha256,
    )
    approved = bundle.model_copy(update={"approval": approval})
    approved.require_review()
    return approved
