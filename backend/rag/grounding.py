"""Đo độ bám sát nguồn KHÔNG qua LLM chấm điểm — dùng để đối chiếu với LLM-judge.

LLM-judge (judge.py) có điểm yếu: AI tự chấm câu trả lời do chính AI (cùng model)
sinh ra nên dễ thiên vị lạc quan. Module này tính một chỉ số độc lập, khách quan:
tỉ lệ câu trong answer có từ ngữ trùng khớp đủ nhiều với context đã retrieve
(word-overlap / Jaccard), không gọi thêm LLM nào — kết quả tái lập được 100%.
"""
import re

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
_WORD_RE = re.compile(r"[0-9a-zA-ZÀ-ỹà-ỹ]+", re.UNICODE)

_STOPWORDS = {
    "và", "là", "của", "có", "the", "a", "an", "trong", "cho", "được", "này",
    "đã", "với", "để", "không", "một", "các", "những", "khi", "như", "về",
    "hay", "hoặc", "nếu", "thì", "đó", "vì", "nên", "ra", "lên", "tại", "từ",
}


def _words(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text) if w.lower() not in _STOPWORDS and len(w) > 1}


def split_sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENT_SPLIT_RE.split(text)]
    return [s for s in parts if len(s) > 3]


def sentence_grounding_score(answer: str, context: str, threshold: float = 0.35) -> dict:
    """Trả về tỉ lệ câu trong `answer` có overlap từ vựng >= threshold với `context`.

    threshold tính theo: |từ chung| / |từ trong câu trả lời| (bao nhiêu % từ của câu
    này thực sự xuất hiện trong nguồn) — không đối xứng, vì context luôn dài hơn câu.
    """
    context_words = _words(context)
    sentences = split_sentences(answer)
    if not sentences:
        return {"grounded": 0, "total": 0, "ratio": None, "per_sentence": []}

    details = []
    grounded = 0
    for s in sentences:
        sw = _words(s)
        if not sw:
            continue
        overlap = len(sw & context_words) / len(sw)
        is_grounded = overlap >= threshold
        grounded += int(is_grounded)
        details.append({"sentence": s, "overlap": round(overlap, 3), "grounded": is_grounded})

    total = len(details)
    return {
        "grounded": grounded,
        "total": total,
        "ratio": (grounded / total) if total else None,
        "per_sentence": details,
    }
