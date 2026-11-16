"""Immutable request evidence and citation identity checks, not semantic grading."""
from __future__ import annotations

import hashlib
import re
from typing import Literal
from pydantic import BaseModel, Field


class EvidenceChunk(tuple):
    """Legacy (text, source, page) row carrying actual index provenance."""
    def __new__(cls, text, source, page, *, chunk_id=None, document_version=None):
        row = super().__new__(cls, (text, source, page))
        row.chunk_id = chunk_id if isinstance(chunk_id, str) and chunk_id else None
        row.document_version = document_version if isinstance(document_version, str) and document_version else None
        return row

    def __getnewargs__(self):
        return tuple(self)


class RetrievedChunks(list):
    def __init__(self, rows=(), *, collection=None, revision=None):
        super().__init__(rows)
        self.collection = collection
        self.revision = revision


class EvidenceOut(BaseModel):
    reference_id: str = Field(pattern=r"^E[1-9][0-9]*$")
    chunk_id: str | None = None
    source: str
    page: int | None = Field(default=None, gt=0)
    document_version: str | None = None
    collection_revision: str | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quote: str


class CitationAudit(BaseModel):
    status: Literal["cited", "uncited", "no_evidence", "invalid"]
    cited_references: list[str] = Field(default_factory=list)
    invalid_references: list[str] = Field(default_factory=list)
    semantic_support_verified: Literal[False] = False


def capture_evidence(rows) -> dict:
    revision = getattr(rows, "revision", None)
    evidence = []
    for number, row in enumerate(rows, 1):
        text, source, page = row
        evidence.append(EvidenceOut(
            reference_id=f"E{number}", chunk_id=getattr(row, "chunk_id", None),
            source=source, page=page, document_version=getattr(row, "document_version", None),
            collection_revision=revision, content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            quote=text,
        ).model_dump())
    return {"schema_version": 1, "collection": getattr(rows, "collection", None), "evidence": evidence}


def evidence_context(bundle: dict) -> str:
    return "\n\n---\n\n".join(
        f"【{item['reference_id']}】 Nguồn: {item['source']}" +
        (f", trang {item['page']}" if item['page'] is not None else "") + "\n" + item['quote']
        for item in bundle['evidence']
    )


def audit_citations(answer: str, bundle: dict) -> dict:
    available = {item['reference_id'] for item in bundle['evidence']}
    tokens = re.findall(r"【(E[^】\r\n]*)】", answer)
    cited = list(dict.fromkeys(token for token in tokens if token in available))
    invalid = list(dict.fromkeys(token for token in tokens if token not in available))
    status = "invalid" if invalid else "cited" if cited else "uncited" if available else "no_evidence"
    return CitationAudit(status=status, cited_references=cited, invalid_references=invalid).model_dump()


def read_bundle(raw: str | None) -> dict:
    import json
    if not raw:
        return {"schema_version": 1, "collection": None, "evidence": [], "citation_audit": None}
    try:
        bundle = json.loads(raw)
        if not isinstance(bundle, dict) or bundle.get('schema_version') != 1:
            raise ValueError("Unknown evidence schema")
        bundle['evidence'] = [EvidenceOut.model_validate(item).model_dump() for item in bundle.get('evidence', [])]
        if len({item['reference_id'] for item in bundle['evidence']}) != len(bundle['evidence']):
            raise ValueError("Duplicate evidence references")
        if any(hashlib.sha256(item['quote'].encode('utf-8')).hexdigest() != item['content_sha256'] for item in bundle['evidence']):
            raise ValueError("Evidence quote fingerprint mismatch")
        if bundle.get('citation_audit') is not None:
            bundle['citation_audit'] = CitationAudit.model_validate(bundle['citation_audit']).model_dump()
        return bundle
    except (ValueError, TypeError):
        return {"schema_version": 1, "collection": None, "evidence": [], "citation_audit": None}



def public_bundle(bundle: dict) -> dict:
    return {"evidence": bundle.get("evidence", []), "citation_audit": bundle.get("citation_audit")}


def evidence_status(bundle: dict, expected_collection: str) -> str:
    from config import CHROMA_DIR
    from rag.document_lifecycle import collection_read_lock, read_revision, DocumentRecoveryError
    items = bundle.get('evidence') or []
    if bundle.get('collection') != expected_collection or not items:
        return "unverified"
    revisions = {item.get('collection_revision') for item in items}
    if len(revisions) != 1 or next(iter(revisions)) in {None, "legacy"}:
        return "unverified"
    if not CHROMA_DIR.is_dir():
        return "unavailable"
    try:
        with collection_read_lock(CHROMA_DIR, expected_collection):
            return "current" if read_revision(CHROMA_DIR, expected_collection) in revisions else "changed"
    except (DocumentRecoveryError, OSError):
        return "unavailable"
