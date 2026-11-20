"""Lexical support diagnostic; this is not a factual/semantic accuracy score.

Word overlap alone cannot verify an answer. We retain negation terms and flag a
simple polarity reversal against the closest source sentence. Paraphrases, scope,
double negation, quantities and more complex contradictions remain unverified.
"""

import re
import unicodedata

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
_WORD_RE = re.compile(r"[0-9a-zA-ZÀ-ỹà-ỹ]+", re.UNICODE)
_NEGATION = {"không", "chưa", "cấm", "not", "never", "no"}

_STOPWORDS = {
    "và",
    "là",
    "của",
    "có",
    "the",
    "a",
    "an",
    "trong",
    "cho",
    "được",
    "này",
    "đã",
    "với",
    "để",
    "một",
    "các",
    "những",
    "khi",
    "như",
    "về",
    "hay",
    "hoặc",
    "nếu",
    "thì",
    "đó",
    "vì",
    "nên",
    "ra",
    "lên",
    "tại",
    "từ",
}


def _words(text: str) -> set[str]:
    # NFC: câu trả lời/ngữ cảnh dạng tổ hợp (NFD) bị cắt vụn ở dấu và trượt chấm điểm.
    text = unicodedata.normalize("NFC", text)
    return {w.lower() for w in _WORD_RE.findall(text) if w.lower() not in _STOPWORDS and len(w) > 1}


def split_sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENT_SPLIT_RE.split(text)]
    return [s for s in parts if len(s) > 3]


def _polarity_mismatch(words: set[str], source_sentences: list[set[str]], threshold: float) -> bool:
    content = words - _NEGATION
    if len(content) < 2:
        return False
    matches = [(len(content & (source - _NEGATION)) / len(content), source) for source in source_sentences]
    best = max((score for score, _ in matches), default=0.0)
    if best < threshold:
        return False
    closest = [source for score, source in matches if score == best]
    # A same-polarity source can support the sentence even if another source
    # contains an unrelated negation.
    return bool(closest) and all(bool(words & _NEGATION) != bool(source & _NEGATION) for source in closest)


def sentence_grounding_score(answer: str, context: str, threshold: float = 0.35) -> dict:
    """Return lexical overlap plus a limited polarity diagnostic.

    The legacy ratio/grounded keys remain for callers; they measure lexical
    support, never semantic accuracy, safety, or correctness.
    """
    context_words = _words(context)
    source_sentences = [_words(s) for s in split_sentences(context)]
    details = []
    grounded = 0
    for sentence in split_sentences(answer):
        words = _words(sentence)
        if not words:
            continue
        overlap = len(words & context_words) / len(words)
        reversal = _polarity_mismatch(words, source_sentences, threshold)
        supported = overlap >= threshold and not reversal
        grounded += int(supported)
        details.append(
            {
                "sentence": sentence,
                "overlap": round(overlap, 3),
                "polarity_mismatch": reversal,
                "grounded": supported,
            }
        )

    total = len(details)
    return {
        "metric": "lexical_support_diagnostic",
        "semantic_accuracy": None,
        "limitations": "Word overlap and simple negation checks cannot verify factual or semantic correctness.",
        "grounded": grounded,
        "total": total,
        "ratio": (grounded / total) if total else None,
        "per_sentence": details,
    }
