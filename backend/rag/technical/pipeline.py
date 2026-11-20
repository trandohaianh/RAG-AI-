"""Parallel hybrid retrieval -> child RRF -> real rerank -> whole parent -> selector.

No free-form technical answer is emitted. Provenance and exact original quotes are
returned for engineer review. Matching/citation integrity is NOT semantic safety.
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import Field

from .adapters import Reranker, Selector, validate_scores
from .models import REFUSAL, Answer, Evidence, Scope, Selection, StrictModel, TechnicalRAGError
from .store import Snapshot

_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="technical-rag")
_CAPACITY = threading.BoundedSemaphore(16)
_QUANTITY = re.compile(r"(?<![\w.,])[-+−]?\d+(?:[.,]\d+)?\s*(?:mA|A|mV|kV|V|mΩ|Ω|kW|W|kHz|MHz|GHz|Hz|°C)(?!\w)")
_CODE = re.compile(r"(?<!\w)[A-Za-z][A-Za-z0-9]*(?:[-_/][A-Za-z0-9]+)*(?!\w)")
_ERROR_NUMBER = re.compile(r"(?:error|mã(?:\s+lỗi)?)\s*[:#-]?\s*(\d+)(?![\w.])", re.I)


class Policy(StrictModel):
    # Calibrate separately per reranker+language+corpus. Scores are not probabilities.
    reranker_id: str = Field(min_length=1)
    calibration_id: str = Field(min_length=1)
    min_score: float = Field(ge=0, le=1)
    candidates_per_branch: int = Field(default=40, ge=1, le=200)
    fused_candidates: int = Field(default=40, ge=1, le=200)
    top_children: int = Field(default=8, ge=1, le=40)
    max_parents: int = Field(default=4, ge=1, le=10)
    context_char_budget: int = Field(default=30000, ge=1000, le=100000)


def _quantity(raw: str) -> str:
    """Chuẩn hoá một đại lượng để so khớp: bỏ khoảng trắng, dấu phẩy thập phân -> dấu chấm.

    Tiếng Việt viết số thập phân bằng dấu phẩy ("12,5 V"). Trước đây mọi câu hỏi có dấu phẩy bị
    từ chối vì "mơ hồ". Chủ sản phẩm chọn (2026-11-17): đọc dấu phẩy là thập phân, áp dụng giống
    nhau cho câu hỏi và tài liệu, nên "12,5 V" khớp "12.5 V". Đánh đổi: "1,500 W" (phân cách
    nghìn) cũng bị đọc là 1.5 — hiếm trong SOP có đơn vị, và vẫn khớp nếu hai bên viết giống nhau.
    """
    return re.sub(r"\s+", "", raw).replace(",", ".").replace("−", "-")


def anchors(query: str) -> tuple[set[str], set[str]]:
    quantities = {_quantity(m.group()) for m in _QUANTITY.finditer(query)}
    codes = {m.group() for m in _CODE.finditer(query) if any(c.isdigit() for c in m.group())}
    codes.update(m.group(1) for m in _ERROR_NUMBER.finditer(query))
    return codes, quantities


def exact_match(query: str, text: str, confirmed_model: str | None = None) -> bool:
    codes, values = anchors(query)
    if confirmed_model and confirmed_model in codes:
        # Discharge the asset identifier only through caller-confirmed equipment scope.
        # Explicit error-code wording still requires that same code in the original row.
        explicit_error = re.search(
            r"(?:error|mã(?:\s+lỗi)?)\s*[:#-]?\s*" + re.escape(confirmed_model) + r"(?!\w)", query, re.I
        )
        if not explicit_error:
            if codes == {confirmed_model} and not re.search(r"\bmodel\b", query, re.I):
                return False  # bare ambiguous code may also be a model; do not guess
            codes.remove(confirmed_model)
    source_values = {_quantity(m.group()) for m in _QUANTITY.finditer(text)}
    return all(re.search(r"(?<![\w.])" + re.escape(c) + r"(?![\w.])", text) for c in codes) and values <= source_values


def rrf(rankings: list[list[str]], limit: int, constant: int = 60) -> list[str]:
    scores = {}
    for ranking in rankings:
        unique = dict.fromkeys(ranking)  # one vote per branch per child
        for rank, cid in enumerate(unique, 1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (constant + rank)
    return sorted(scores, key=lambda cid: (-scores[cid], cid))[:limit]


class TechnicalRAG:
    def __init__(
        self, root: Path, embeddings, embedding_id: str, reranker: Reranker, selector: Selector, policy: Policy
    ):
        if reranker.identity != policy.reranker_id:
            raise ValueError("Policy must be calibrated for this exact reranker identity")
        self.root, self.embeddings, self.embedding_id = Path(root), embeddings, embedding_id
        self.reranker, self.selector, self.policy = reranker, selector, policy

    def retrieve(self, snapshot: Snapshot, query: str, source_id: str | None = None) -> list[Evidence]:
        p = self.policy
        vector = _EXECUTOR.submit(snapshot.vector, query, p.candidates_per_branch, source_id)
        lexical = _EXECUTOR.submit(snapshot.bm25, query, p.candidates_per_branch, source_id)
        rankings, errors = [], []
        # Always join both readers. Failure of either branch must not look like a full pipeline.
        for future in (vector, lexical):
            try:
                rankings.append(future.result())
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise TechnicalRAGError("Hybrid retrieval branch unavailable") from errors[0]
        candidates = []
        has_anchors = any(anchors(query))
        for cid in rrf(rankings, p.fused_candidates):
            child, parent = snapshot.resolve(cid)
            if source_id and parent.source_id != source_id:
                continue
            if not exact_match(query, child.text, snapshot.scope.model):
                continue
            if has_anchors and not child.anchor_eligible:
                continue
            candidates.append((child, parent))
        if not candidates:
            return []
        # Always invoke a real cross-encoder, including a single-candidate request.
        scores = validate_scores(self.reranker.score(query, [c.retrieval_text for c, _ in candidates]), len(candidates))
        ranked = sorted(zip(candidates, scores, strict=False), key=lambda row: (-row[1], row[0][0].child_id))
        evidence, seen = [], set()
        for (_child, parent), score in ranked[: p.top_children]:
            if score < p.min_score or parent.parent_id in seen:
                continue
            seen.add(parent.parent_id)
            evidence.append(Evidence(reference_id="E" + str(len(evidence) + 1), parent=parent, best_child_score=score))
        # Requiring a source selector prevents mixing independently authored manuals.
        if len({e.parent.source_id for e in evidence}) > 1:
            raise TechnicalRAGError("Ambiguous sources; select a reviewed source_id")
        # Nhiều mục liên quan là dấu hiệu bằng chứng DỒI DÀO, không phải lý do từ chối: giữ các mục
        # điểm cao nhất (evidence đã xếp theo điểm) trong giới hạn số mục và ngân sách ký tự. Mỗi
        # mục vẫn được đưa NGUYÊN VẸN; chỉ từ chối khi ngay mục tốt nhất cũng không vừa.
        kept, used = [], 0
        for item in evidence[: p.max_parents]:
            if used + len(item.parent.text) > p.context_char_budget:
                break
            kept.append(item)
            used += len(item.parent.text)
        if evidence and not kept:
            raise TechnicalRAGError("Whole required parents exceed context budget")
        return kept  # là tiền tố của evidence nên nhãn E1..En vẫn liên tục

    def answer(self, query: str, scope: Scope, source_id: str | None = None) -> Answer:
        if not query.strip() or len(query) > 4000:
            return Answer(status="abstained", text=REFUSAL, reason="invalid_query")
        if not _CAPACITY.acquire(blocking=False):
            return Answer(status="abstained", text=REFUSAL, reason="busy")
        generation = None
        try:
            anchors(query)  # validate before embedding or network calls
            snapshot = Snapshot(self.root, scope, self.embeddings, self.embedding_id)
            generation = snapshot.generation
            evidence = self.retrieve(snapshot, query, source_id)
            if not evidence:
                return Answer(status="abstained", text=REFUSAL, generation=generation, reason="no_evidence")
            context = [
                {"id": e.reference_id, "source": e.parent.source_name, "pages": e.parent.pages, "text": e.parent.text}
                for e in evidence
            ]
            selection = self.selector.select(query, scope, context)
            selection = (
                Selection.model_validate(selection.model_dump())
                if isinstance(selection, Selection)
                else Selection.model_validate(selection)
            )
            ids = selection.evidence_ids
            allowed = {e.reference_id: e for e in evidence}
            if (
                not selection.enough_information
                or not ids
                or len(ids) != len(set(ids))
                or any(i not in allowed for i in ids)
            ):
                raise TechnicalRAGError("Insufficient or invalid evidence selection")
            selected = [allowed[i] for i in ids]
            # These are complete canonical source quotes, never model-generated numbers.
            parts = []
            for e in selected:
                page = ",".join(map(str, e.parent.pages)) or "không có số trang vật lý"
                parts.append(
                    f"【{e.reference_id}】 {e.parent.source_name}; trang {page}; "
                    f"manual {scope.manual_revision}; SHA-256 {e.parent.text_sha256}\n" + e.parent.text
                )
            return Answer(status="evidence", text="\n\n".join(parts), generation=generation, evidence=selected)
        except Exception as exc:
            # Never expose provider text, keys or unvalidated partial technical answers.
            return Answer(status="abstained", text=REFUSAL, generation=generation, reason=type(exc).__name__)
        finally:
            _CAPACITY.release()
