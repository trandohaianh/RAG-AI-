"""Provider thay thế cho chế độ offline: không gọi mạng, kết quả lặp lại được.

Không đo chất lượng model thật. Mục đích: chạy TOÀN BỘ đường ống của ứng dụng
(ingest → Chroma + BM25 → RRF → rerank → prompt → kiểm tra trích dẫn) trong CI,
và kiểm tra chính bộ chấm điểm. Chất lượng câu trả lời thật đo bằng chế độ live.
"""
from __future__ import annotations

import hashlib
import math
import re

from evals.graders import content_tokens, normalize

DIM = 512
REFUSAL_TEXT = "Thông tin không có trong tài liệu đã upload; hãy bổ sung tài liệu phù hợp."


class HashingEmbeddings:
    """Embedding băm từ + trigram ký tự (feature hashing), chuẩn hoá L2."""

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * DIM
        norm = normalize(text)
        features = content_tokens(text)
        compact = norm.replace(" ", "_")
        features += [compact[i:i + 3] for i in range(max(0, len(compact) - 2))]
        for feat in features:
            digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % DIM
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign
        length = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / length for v in vec]

    def embed_documents(self, texts: list[str], *args, **kwargs) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str, *args, **kwargs) -> list[float]:
        return self._vector(text)


_EVIDENCE_RE = re.compile(r"【(E\d+)】 Nguồn: [^\n]*\n(.*?)(?=\n\n【E\d+】|\Z)", re.S)
_CODE_RE = re.compile(r"\b[\w]*\d[\w-]*\b")


def _split_prompt(prompt: str) -> tuple[str, str]:
    """Tách phần Context và câu hỏi từ prompt do rag.prompt.build_gemini_prompt tạo."""
    head, _, tail = prompt.rpartition("Câu hỏi:")
    context = head.split("Context tài liệu:", 1)[-1].split("Lịch sử hội thoại:", 1)[0]
    question = tail.split("Trả lời", 1)[0]
    return context, question


def extractive_answer(prompt: str, *, min_overlap: float = 0.35, max_sentences: int = 2) -> str:
    """Chọn câu trong bằng chứng trùng nhiều từ với câu hỏi nhất và gắn nhãn 【E#】.

    Từ chối khi (a) câu hỏi nhắc một mã/số hiệu (E-999, X99...) không có trong bằng chứng,
    hoặc (b) không câu nào phủ đủ từ nội dung của câu hỏi.
    """
    context, question = _split_prompt(prompt)
    q_tokens = set(content_tokens(question))
    blocks = _EVIDENCE_RE.findall(context.strip())
    evidence_norm = normalize(" ".join(quote for _, quote in blocks))
    codes = {normalize(c) for c in _CODE_RE.findall(question)}
    if any(code and code not in evidence_norm for code in codes):
        return REFUSAL_TEXT
    candidates: list[tuple[float, int, str, str]] = []
    for order, (ref, quote) in enumerate(blocks):
        for sentence in re.split(r"(?<=[.;])\s+|\n+", quote):
            tokens = set(content_tokens(sentence))
            if len(tokens) < 4 or not q_tokens:
                continue  # bỏ tiêu đề mục ngắn
            score = len(tokens & q_tokens) / len(q_tokens)
            candidates.append((score, -order, ref, sentence.strip()))
    candidates.sort(reverse=True)
    if not candidates or candidates[0][0] < min_overlap:
        return REFUSAL_TEXT
    chosen = [f"{sentence} 【{ref}】" for score, _, ref, sentence in candidates[:max_sentences]
              if score >= min_overlap / 2]
    return " ".join(chosen)


def fake_generate_text(prompt: str, **_kwargs) -> str:
    from costs.recorder import estimate_tokens, record_usage

    answer = extractive_answer(prompt)
    record_usage(provider="local", model="local-extractive", input_tokens=estimate_tokens(prompt),
                 output_tokens=estimate_tokens(answer), estimated=True, latency_ms=0)
    return answer
