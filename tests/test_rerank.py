"""Tests rerank fallback (không gọi Cohere khi top_n >= số chunk)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from rag.rerank import rerank_chunks


def test_rerank_short_circuit_no_api():
    """len(chunks) <= top_n → không gọi Cohere."""
    chunks = [
        ("text a", "f1.pdf", 1),
        ("text b", "f1.pdf", 2),
    ]
    out = rerank_chunks("query", chunks, top_n=5)
    assert len(out) == 2
    assert out[0][0] == "text a"
