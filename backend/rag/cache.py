"""Redis cache cho kết quả retrieve RAG (tuần 1–2)."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import TYPE_CHECKING

from config import REDIS_CACHE_TTL_SEC, REDIS_URL
from rag.evidence import EvidenceChunk

if TYPE_CHECKING:
    pass

_client = None
_pool = None
_lock = threading.RLock()
_retry_after = 0.0


def _close_owned(client, pool):
    try:
        if client is not None:
            client.close()
    finally:
        if pool is not None:
            pool.disconnect()


def close_cache() -> None:
    """Call after request draining. This module owns both client and shared pool."""
    global _client, _pool, _retry_after
    with _lock:
        client, pool = _client, _pool
        _client = _pool = None
        _retry_after = 0.0
        _close_owned(client, pool)


def _redis():
    global _client, _pool, _retry_after
    if not REDIS_URL:
        return None
    with _lock:
        if _client is not None:
            return _client
        if time.monotonic() < _retry_after:
            return None
        import redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        pool = client = None
        try:
            pool = redis.ConnectionPool.from_url(
                REDIS_URL,
                decode_responses=True,
                max_connections=32,
                socket_connect_timeout=0.3,
                socket_timeout=0.3,
                retry=Retry(NoBackoff(), 0),
                retry_on_timeout=False,
                health_check_interval=0,
                socket_keepalive=True,
            )
            client = redis.Redis(connection_pool=pool)
            client.ping()
        except Exception:
            try:
                _close_owned(client, pool)
            except Exception:
                pass
            _retry_after = time.monotonic() + 1.0
            return None
        _client, _pool = client, pool
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
        json.dumps([scope, k, source, query], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:48]
    return f"rag:retrieve:{scope}:v3:{h}"


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
            out.append(
                EvidenceChunk(
                    row["text"],
                    row["source"],
                    int(page) if page is not None else None,
                    chunk_id=row.get("chunk_id"),
                    document_version=row.get("document_version"),
                )
            )
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
            {
                "text": row[0],
                "source": row[1],
                "page": row[2],
                "chunk_id": getattr(row, "chunk_id", None),
                "document_version": getattr(row, "document_version", None),
            }
            for row in chunks
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
        for key in r.scan_iter(match=f"rag:retrieve:workspace:{workspace_id}:*", count=100):
            r.delete(key)
    except Exception:
        pass
