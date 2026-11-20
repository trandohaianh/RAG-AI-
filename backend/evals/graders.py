"""Bộ chấm điểm xác định (deterministic) cho câu trả lời RAG tiếng Việt.

Không dùng LLM nên chạy được trong CI và cho cùng kết quả mỗi lần. LLM-judge là tuỳ chọn
và nằm riêng ở harness (live --judge).
"""

from __future__ import annotations

import math
import re
import unicodedata

_STOPWORDS = {
    "la",
    "va",
    "cua",
    "co",
    "khong",
    "cho",
    "nhung",
    "nao",
    "gi",
    "the",
    "thi",
    "duoc",
    "khi",
    "trong",
    "mot",
    "cac",
    "can",
    "phai",
    "voi",
    "de",
    "nay",
    "do",
    "ve",
    "hay",
    "hoac",
    "neu",
    "da",
    "dang",
    "se",
    "tu",
    "theo",
    "tai",
    "o",
    "ra",
    "vao",
    "len",
    "nhu",
    "bang",
    "lam",
    "minh",
    "toi",
    "ban",
    "chung",
    "vay",
    "sao",
    "bao",
    "nhieu",
    "ma",
    "con",
    "chua",
    "nhat",
}

_REFUSAL_PATTERNS = [
    r"khong co trong tai lieu",
    r"khong (?:tim thay|thay) (?:thong tin|noi dung|tai lieu)",
    r"tai lieu (?:da upload |da tai len |mau )?khong (?:cung cap|neu|de cap|co thong tin|chua)",
    r"khong (?:du|co du) (?:thong tin|bang chung|du lieu)",
    r"chua co (?:du )?(?:thong tin|bang chung|tai lieu)",
    r"chi tra loi dua tren tai lieu",
    r"khong the tra loi",
    r"ngoai pham vi tai lieu",
]


def normalize(text: str) -> str:
    """lowercase + bỏ dấu tiếng Việt + gộp khoảng trắng; giữ chữ/số/_ để khớp mã như client_action_id."""
    text = (text or "").replace("Đ", "D").replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.lower()
    text = re.sub(r"[^\w\s\-/:.]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def content_tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[\w\-]+", normalize(text)) if len(t) > 1 and t not in _STOPWORDS]


# Từ phủ định đứng ngay trước một cụm (đã bỏ dấu): "không được tắt nguồn" không phải là
# "tắt nguồn". Trước đây cụm bị phủ định vẫn tính là khớp: dữ kiện đúng nghĩa ngược vẫn "đạt",
# và lời cảnh báo an toàn lại bị tính là nội dung cấm.
# Chỉ dùng "không ...": sau khi bỏ dấu, "đừng/đúng", "chưa/chứa", "cấm/cầm" trùng chữ nhau.
# Cho phép các trợ từ tình thái chen giữa ("không được PHÉP tắt nguồn", "không NÊN tự ý ...").
_NEGATION_TAIL = re.compile(
    r"(?:^|\s)(?:khong(?:\s+(?:duoc|phep|nen|the|bao|gio|can|tu|y|hay))*|never|do not|don t|not)\s*$"
)


def _contains(haystack_norm: str, phrase: str) -> bool:
    needle = normalize(phrase)
    if not needle:
        return False
    negated_phrase = _NEGATION_TAIL.search(" " + needle.split(" ")[0]) is not None
    for match in re.finditer(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack_norm):
        before = haystack_norm[max(0, match.start() - 24) : match.start()]
        if negated_phrase or not _NEGATION_TAIL.search(before):
            return True
    return False


def fact_coverage(answer: str, facts: list[list[str]]) -> tuple[float | None, list[list[str]]]:
    """Tỉ lệ nhóm dữ kiện xuất hiện trong câu trả lời và danh sách nhóm bị thiếu."""
    if not facts:
        return None, []
    norm = normalize(answer)
    missing = [group for group in facts if not any(_contains(norm, alt) for alt in group)]
    return (len(facts) - len(missing)) / len(facts), missing


def forbidden_hits(answer: str, phrases: list[str]) -> list[str]:
    norm = normalize(answer)
    return [p for p in phrases if _contains(norm, p)]


def is_refusal(answer: str) -> bool:
    """Câu trả lời là lời từ chối: có cụm từ chối và KHÔNG trích dẫn bằng chứng nào.

    Một câu trả lời có nhãn 【E#】 đang khẳng định điều gì đó từ tài liệu; nếu chỉ vì có thêm
    câu "không thể trả lời ..." mà tính là từ chối, câu bịa kèm lời rào đón sẽ đạt bài "phải từ chối".
    """
    if re.search(r"【\s*E\d+\s*】", answer or ""):
        return False
    norm = normalize(answer)
    return any(re.search(pattern, norm) for pattern in _REFUSAL_PATTERNS)


def _sentences(answer: str) -> list[str]:
    cleaned = re.sub(r"【E\d+】", " ", answer or "")
    parts = re.split(r"(?<=[.!?;\n])\s+|\n+|^\s*[-*•]\s+", cleaned)
    return [p.strip(" -*•") for p in parts if len(content_tokens(p)) >= 3]


def groundedness(answer: str, contexts: list[str], threshold: float = 0.6) -> float | None:
    """Tỉ lệ câu trong câu trả lời có ≥60% từ nội dung xuất hiện trong ngữ cảnh truy xuất.

    Đây là proxy từ vựng (lexical), không phải kiểm chứng ngữ nghĩa: phát hiện tốt câu bịa
    thêm thông số/lệnh không có trong tài liệu, nhưng không bắt được suy luận sai từ đúng từ.
    """
    sentences = [s for s in _sentences(answer) if not is_refusal(s)]
    if not sentences:
        return None
    vocab = set()
    ctx_sentences = []
    for ctx in contexts:
        vocab.update(content_tokens(ctx))
        ctx_sentences.extend(set(content_tokens(s)) | ({"khong"} if _negated(s) else set()) for s in _sentences(ctx))
    supported = 0
    for sentence in sentences:
        tokens = content_tokens(sentence)
        if (
            tokens
            and sum(t in vocab for t in tokens) / len(tokens) >= threshold
            and not _flips_polarity(set(tokens), _negated(sentence), ctx_sentences)
        ):
            supported += 1
    return supported / len(sentences)


def _negated(text: str) -> bool:
    return re.search(r"(?<![\w])khong(?![\w])", normalize(text)) is not None


def _flips_polarity(tokens: set[str], negated: bool, ctx_sentences: list[set[str]]) -> bool:
    """Câu trả lời đảo nghĩa câu nguồn gần nhất ("Không tắt nguồn" vs "Tắt nguồn").

    "khong" là stopword nên phép đếm từ ở trên không thấy nó; trước đây câu trả lời phủ định
    ngược tài liệu vẫn được tính là "có căn cứ" 100%.
    """
    if not ctx_sentences:
        return False
    best = max(ctx_sentences, key=lambda c: len(tokens & c))
    if not tokens & best:
        return False
    return negated != ("khong" in best)


# --------------------------------------------------------------------------- retrieval
def ranked_unique_sources(sources: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for src in sources:
        if src not in seen:
            seen.add(src)
            out.append(src)
    return out


def retrieval_scores(ranked_sources: list[str], expected: list[str], k: int) -> dict | None:
    """Recall@k, precision@k, MRR, nDCG@k ở mức tài liệu nguồn (relevance nhị phân)."""
    if not expected:
        return None
    ranked = ranked_unique_sources(ranked_sources)[:k]
    relevant = set(expected)
    hits = [1 if src in relevant else 0 for src in ranked]
    found = sum(hits)
    first = next((i for i, h in enumerate(hits, 1) if h), None)
    dcg = sum(h / math.log2(i + 1) for i, h in enumerate(hits, 1))
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return {
        "recall": found / len(relevant),
        "precision": found / len(ranked) if ranked else 0.0,
        "mrr": 1 / first if first else 0.0,
        "ndcg": dcg / ideal if ideal else 0.0,
        "hit": 1.0 if found else 0.0,
    }


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = (len(ordered) - 1) * q
    lo, hi = math.floor(idx), math.ceil(idx)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo)
