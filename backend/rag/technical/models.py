"""Reviewed canonical extraction models; approval is an operator assertion, not PKI."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RETRIEVAL_FORMAT = "asset-section-v1"

REFUSAL = "Tôi không có đủ thông tin từ hồ sơ kỹ thuật để giải quyết mã lỗi này"


class TechnicalRAGError(RuntimeError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


class Scope(StrictModel):
    # Supply from authenticated tenancy + engineer-confirmed asset, never from an LLM.
    tenant: str = Field(min_length=1)
    workspace: str = Field(min_length=1)
    vendor: str = Field(min_length=1)
    model: str = Field(min_length=1)
    hardware_revision: str = Field(min_length=1)
    manual_revision: str = Field(min_length=1)

    @property
    def key(self) -> str:
        return digest(canonical(self.model_dump()))

    @property
    def namespace(self) -> str:
        return digest(canonical([self.tenant, self.workspace]))


class Block(StrictModel):
    kind: Literal["title", "text", "table", "diagram"]
    text: str = Field(min_length=1)
    section: str = Field(min_length=1)
    pages: list[int] = Field(default_factory=list)
    # For simple tables only: contiguous HTML source slices, header then complete rows.
    row_spans: list[list[int]] = Field(default_factory=list)
    header_rows: int = Field(default=0, ge=0)
    # A reviewer must resolve mixed models/conditions, OCR, figures and numbering.
    single_equipment: bool = False


class Approval(StrictModel):
    reviewer: str = Field(min_length=1)
    reviewed_at: str = Field(min_length=1)
    extraction_sha256: str
    raw_sha256: str


class LayoutBundle(StrictModel):
    source_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    raw_sha256: str
    parser: str = Field(min_length=1)
    scope: Scope
    blocks: list[Block] = Field(min_length=1)
    # Set true only after verifying the full asset/revision and transcription.
    scope_reviewed: bool = False
    approval: Approval | None = None

    def extraction_hash(self) -> str:
        return digest(canonical(self.model_dump(exclude={"approval"})))

    def require_review(self) -> None:
        a = self.approval
        if (not a or not self.scope_reviewed or a.raw_sha256 != self.raw_sha256
                or a.extraction_sha256 != self.extraction_hash()):
            raise TechnicalRAGError("Unreviewed or modified extraction")
        if len(self.raw_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.raw_sha256):
            raise TechnicalRAGError("Invalid raw SHA-256")
        if any(b.kind == "diagram" or not b.single_equipment for b in self.blocks):
            raise TechnicalRAGError("Unresolved figure or equipment/condition scope")
        for b in self.blocks:
            if any(type(p) is not int or p < 1 for p in b.pages):
                raise TechnicalRAGError("Invalid physical page number")


class Parent(StrictModel):
    parent_id: str
    source_id: str
    source_name: str
    raw_sha256: str
    extraction_sha256: str
    scope_key: str
    text: str
    text_sha256: str
    pages: list[int]


class Child(StrictModel):
    child_id: str
    parent_id: str
    text: str
    spans: list[list[int]]
    anchor_eligible: bool = True
    context_prefix: str = ""

    @property
    def retrieval_text(self) -> str:
        # Search-only metadata: never presented as a continuous original source quote.
        return self.context_prefix + "\n" + self.text


class Selection(StrictModel):
    enough_information: bool
    evidence_ids: list[str]


class Evidence(StrictModel):
    reference_id: str
    parent: Parent
    best_child_score: float


class Answer(StrictModel):
    status: Literal["evidence", "abstained"]
    text: str
    generation: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    reason: str | None = None
    requires_engineer_review: bool = True
    semantic_correctness_verified: Literal[False] = False
