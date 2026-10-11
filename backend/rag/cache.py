"""Redis cache cho kết quả retrieve RAG (tuần 1–2)."""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from config import REDIS_CACHE_TTL_SEC, REDIS_URL

if TYPE_CHECKING:
    pass

_client = None


def _redis():
    global _client
    if not REDIS_URL:
        return None
    if _client is None:
        import redis

        try:
            _client = redis.from_url(REDIS_URL, decode_responses=True)
            _client.ping()
        except Exception:
            _client = None
            return None
    return _client


def cache_key_retrieve(
    user_id: int,
    query: str,
    k: int,
    source: str | None = None,
    workspace_id: int | None = None,
) -> str:
    scope = f"workspace:{workspace_id}" if workspace_id is not None else f"user:{user_id}"
    h = hashlib.sha256(
        f"{scope}:{k}:{source or ''}:{query}".encode("utf-8")
    ).hexdigest()[:48]
    return f"rag:retrieve:{scope}:{h}"


def get_cached_chunks(
    user_id: int,
    query: str,
    k: int,
    source: str | None = None,
    workspace_id: int | None = None,
) -> list[tuple[str, str, int | None]] | None:
    r = _redis()
    if not r:
        return None
    try:
        raw = r.get(cache_key_retrieve(user_id, query, k, source, workspace_id))
        if not raw:
            return None
        data = json.loads(raw)
        out: list[tuple[str, str, int | None]] = []
        for row in data:
            page = row.get("page")
            out.append((row["text"], row["source"], int(page) if page is not None else None))
        return out
    except Exception:
        return None


def set_cached_chunks(
    user_id: int,
    query: str,
    k: int,
    chunks: list[tuple[str, str, int | None]],
    source: str | None = None,
    workspace_id: int | None = None,
) -> None:
    r = _redis()
    if not r:
        return
    try:
        payload = [
            {"text": t, "source": s, "page": p} for t, s, p in chunks
        ]
        r.setex(
            cache_key_retrieve(user_id, query, k, source, workspace_id),
            REDIS_CACHE_TTL_SEC,
            json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        pass


def invalidate_user_cache(user_id: int) -> None:
    """Xóa cache retrieve của user (sau upload/xóa doc)."""
    r = _redis()
    if not r:
        return
    try:
        for key in r.scan_iter(match=f"rag:retrieve:user:{user_id}:*", count=100):
            r.delete(key)
    except Exception:
        pass


def invalidate_workspace_cache(workspace_id: int) -> None:
    """Xóa cache retrieval dùng chung của workspace sau upload/xóa tài liệu."""
    r = _redis()
    if not r:
        return
    try:
        for key in r.scan_iter(
            match=f"rag:retrieve:workspace:{workspace_id}:*", count=100
        ):
            r.delete(key)
    except Exception:
        pass
