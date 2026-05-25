"""Cohere Rerank — xếp lại chunk theo relevance (tuần 1–2)."""
from __future__ import annotations

import httpx

from config import COHERE_API_KEY, COHERE_RERANK_MODEL, TOP_K


def rerank_chunks(
    query: str,
    chunks: list[tuple[str, str, int | None]],
    top_n: int | None = None,
) -> list[tuple[str, str, int | None]]:
    """
    chunks: (text, source, page)
    Trả về top_n chunk sau rerank; nếu không có API key → trả nguyên bản (cắt top_n).
    """
    top_n = top_n or TOP_K
    if not chunks:
        return []
    if not COHERE_API_KEY or len(chunks) <= top_n:
        return chunks[:top_n]

    documents = [c[0][:8000] for c in chunks]  # giới hạn Cohere
    try:
        resp = httpx.post(
            "https://api.cohere.com/v1/rerank",
            headers={
                "Authorization": f"Bearer {COHERE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": COHERE_RERANK_MODEL,
                "query": query[:2000],
                "documents": documents,
                "top_n": min(top_n, len(chunks)),
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        out: list[tuple[str, str, int | None]] = []
        for item in results:
            idx = item.get("index")
            if idx is None or idx < 0 or idx >= len(chunks):
                continue
            out.append(chunks[idx])
        return out if out else chunks[:top_n]
    except Exception:
        return chunks[:top_n]
