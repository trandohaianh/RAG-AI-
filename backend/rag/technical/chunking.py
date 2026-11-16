"""Structure first: an entire reviewed section is one parent, never a hard slice."""
from __future__ import annotations

import re
from collections import OrderedDict

from .models import Child, LayoutBundle, Parent, TechnicalRAGError, canonical, digest


def _windows(text: str, target: int):
    # Whole whitespace-delimited tokens: do not split E01, model IDs or numeric strings.
    # Windows cover ALL source characters; overlap repeats whole tokens.
    tokens = list(re.finditer(r"\S+\s*|^\s+", text))
    i = 0
    while i < len(tokens):
        j = i + 1
        while j < len(tokens) and tokens[j].end() - tokens[i].start() <= target:
            j += 1
        start, end = tokens[i].start(), tokens[j - 1].end()
        yield start, end
        if j == len(tokens):
            break
        i = max(i + 1, j - 2)  # about two tokens of overlap, not arbitrary character cuts


def chunk_bundle(bundle: LayoutBundle, child_target: int = 200,
                 parent_hard_limit: int = 12000) -> tuple[list[Parent], list[Child]]:
    bundle.require_review()
    if child_target < 32 or parent_hard_limit < child_target:
        raise ValueError("Invalid chunk budgets")
    sections = OrderedDict()
    # Sections must have been checked by a human: automatic titles can split procedures.
    last_section = None
    for block in bundle.blocks:
        if block.section in sections and block.section != last_section:
            raise TechnicalRAGError("Non-contiguous section ID")
        sections.setdefault(block.section, []).append(block)
        last_section = block.section
    parents, children = [], []
    for section, blocks in sections.items():
        text = "\n\n".join(b.text for b in blocks)
        if len(text) > parent_hard_limit:
            raise TechnicalRAGError("Oversize atomic section: review structural boundaries; never truncate")
        pid = digest(canonical([bundle.scope.key, bundle.source_id, bundle.raw_sha256,
                                bundle.extraction_hash(), section, text]))
        parent = Parent(parent_id=pid, source_id=bundle.source_id, source_name=bundle.source_name,
                        raw_sha256=bundle.raw_sha256, extraction_sha256=bundle.extraction_hash(),
                        scope_key=bundle.scope.key, text=text, text_sha256=digest(text),
                        pages=sorted({p for b in blocks for p in b.pages}))
        parents.append(parent)
        prefix = (f"Model: {bundle.scope.model}; hardware: {bundle.scope.hardware_revision}; "
                  f"manual: {bundle.scope.manual_revision}\n"
                  + "\n".join(b.text for b in blocks if b.kind == "title"))
        offset = 0
        for block in blocks:
            groups = []
            if block.kind == "table":
                spans = block.row_spans
                if (not spans or not 1 <= block.header_rows < len(spans)
                        or any(len(s) != 2 or not 0 <= s[0] < s[1] <= len(block.text) for s in spans)
                        or any(a[1] > b[0] for a, b in zip(spans, spans[1:]))):
                    raise TechnicalRAGError("Table requires reviewed header and complete row offsets")
                # HTML with merged cells needs a manually resolved, reviewed representation.
                if re.search(r"(?:rowspan|colspan)\s*=", block.text, re.I):
                    raise TechnicalRAGError("Merged table requires reviewed transcription")
                from .layout import table_layout
                checked_text, checked_spans, _ = table_layout(block.text)
                if checked_text != block.text or checked_spans != spans:
                    raise TechnicalRAGError("Table row offsets must describe complete canonical rows")
                headers = spans[:block.header_rows]
                groups = [headers + [row] for row in spans[block.header_rows:]]
                # Keep caption, HTML wrapper, footnotes searchable too, without asserting a row link.
                groups += [[[s, e]] for s, e in _windows(block.text, child_target)]
            else:
                groups = [[[s, e]] for s, e in _windows(block.text, child_target)]
            for group_no, group in enumerate(groups):
                absolute = [[offset + s, offset + e] for s, e in group]
                child_text = "\n".join(text[s:e] for s, e in absolute)
                cid = digest(canonical([pid, offset, group_no, absolute, child_text]))
                children.append(Child(child_id=cid, parent_id=pid, text=child_text, spans=absolute, context_prefix=prefix,
                                      anchor_eligible=block.kind != "table" or group_no < len(block.row_spans) - block.header_rows))
            offset += len(block.text) + 2
    return parents, children
