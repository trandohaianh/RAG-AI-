"""Retriever lai: (vector search ‖ BM25) → hợp nhất RRF → cache Redis → rerank.

BM25 chạy SONG SONG với tìm vector ở PHA TRUY XUẤT, không phải ở pha xếp hạng lại.
`rag.rerank` chỉ xếp lại nhóm ứng viên đã lấy về, nên nếu đoạn chứa đúng mã lỗi mà
tìm vector bỏ sót thì nó không có mặt để mà xếp. Xem `rag/bm25.py`.
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import chromadb

from config import CHROMA_DIR, RETRIEVE_CANDIDATES, TOP_K
from rag.bm25 import bm25_search, reciprocal_rank_fusion
from rag.evidence import EvidenceChunk, RetrievedChunks
from rag.cache import get_cached_chunks, set_cached_chunks
from rag.document_lifecycle import (
    DocumentRecoveryError, collection_read_lock, read_revision, sync_bm25_revision,
)
from rag.ingest import _collection_name, get_vectorstore
from rag.rerank import rerank_chunks

logger = logging.getLogger(__name__)
_SEARCH_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="rag-search")


def _search_sync(
    user_id: int,
    query: str,
    k: int,
    source: str | None = None,
    workspace_id: int | None = None,
) -> list[tuple[str, str, int | None]]:
    store = get_vectorstore(user_id, workspace_id)
    where = {"source": source} if source else None
    docs = store.similarity_search(query, k=k, filter=where)
    results: list[tuple[str, str, int | None]] = []
    for doc in docs:
        source_out = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page")
        page_out = int(page) + 1 if page is not None else None
        results.append(EvidenceChunk(doc.page_content, source_out, page_out, chunk_id=getattr(doc, "id", None), document_version=doc.metadata.get("document_version")))
    return results


def _bm25_search_sync(
    user_id: int,
    query: str,
    k: int,
    source: str | None = None,
    workspace_id: int | None = None,
) -> list[tuple[str, str, int | None]]:
    """Keyword branch; failures are isolated by the hybrid coordinator."""
    name = _collection_name(user_id, workspace_id)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_collection(name)
    return bm25_search(collection, name, query, k, source, raise_errors=True)


def _hybrid_search_sync(
    user_id: int,
    query: str,
    k: int,
    source: str | None = None,
    workspace_id: int | None = None,
) -> list[tuple[str, str, int | None]]:
    # A shared executor bounds worker count while preserving both retrieval branches.
    # bind_context: executor không tự mang contextvars → embedding câu hỏi vẫn gán đúng user.
    from costs.context import bind_context

    vector = _SEARCH_EXECUTOR.submit(bind_context(_search_sync), user_id, query, k, source, workspace_id)
    lexical = _SEARCH_EXECUTOR.submit(_bm25_search_sync, user_id, query, k, source, workspace_id)
    rankings = []
    failures = []
    # Read both futures even after one fails: the collection snapshot must remain
    # locked until every branch has finished reading it.
    for branch, future in [("vector", vector), ("BM25", lexical)]:
        try:
            rankings.append(future.result())
        except Exception as exc:
            failures.append(exc)
            rankings.append([])
            logger.warning("%s retrieval unavailable (%s)", branch, type(exc).__name__)
    for error in failures:
        if isinstance(error, DocumentRecoveryError):
            raise error
    if len(failures) == 2:
        raise RuntimeError("Both retrieval branches failed") from failures[0]
    vector_hits, lexical_hits = rankings
    if not vector_hits:
        return lexical_hits
    if not lexical_hits:
        return vector_hits
    from rag.rerank import HybridRanking

    return HybridRanking(reciprocal_rank_fusion([vector_hits, lexical_hits], k=k))


def _read_snapshot(
    user_id: int, query: str, k: int, source: str | None,
    workspace_id: int | None, final_k: int | None = None,
):
    collection_name = _collection_name(user_id, workspace_id)
    with collection_read_lock(CHROMA_DIR, collection_name):
        revision = read_revision(CHROMA_DIR, collection_name)
        sync_bm25_revision(CHROMA_DIR, collection_name, revision)
        cache_query = f"{revision}:{query}"
        if final_k is not None:
            cached = get_cached_chunks(user_id, cache_query, final_k, source, workspace_id)
            if cached is not None:
                return cached[:final_k], revision, True
        return _hybrid_search_sync(user_id, query, k, source, workspace_id), revision, False


async def _hybrid_search(
    user_id: int,
    query: str,
    k: int,
    source: str | None = None,
    workspace_id: int | None = None,
) -> list[tuple[str, str, int | None]]:
    rows, _, _ = await asyncio.to_thread(_read_snapshot, user_id, query, k, source, workspace_id)
    return rows


def _cache_snapshot(
    user_id: int, query: str, final_k: int, ranked,
    source: str | None, workspace_id: int | None, revision: str,
) -> None:
    collection_name = _collection_name(user_id, workspace_id)
    with collection_read_lock(CHROMA_DIR, collection_name):
        if read_revision(CHROMA_DIR, collection_name) == revision:
            set_cached_chunks(user_id, f"{revision}:{query}", final_k, ranked, source, workspace_id)


async def retrieve(
    user_id: int,
    query: str,
    k: int | None = None,
    source: str | None = None,
    workspace_id: int | None = None,
) -> list[tuple[str, str, int | None]]:
    final_k = k or TOP_K
    candidates = max(RETRIEVE_CANDIDATES, final_k)
    raw, revision, cached = await asyncio.to_thread(
        _read_snapshot, user_id, query, candidates, source, workspace_id, final_k
    )
    if cached:
        return RetrievedChunks(raw, collection=_collection_name(user_id, workspace_id), revision=revision)
    # Rerank can call an external service; it must neither block the event loop
    # nor keep the publication lock after the retrieval snapshot is complete.
    ranked = await asyncio.to_thread(rerank_chunks, query, raw, top_n=final_k)
    await asyncio.to_thread(
        _cache_snapshot, user_id, query, final_k, ranked, source, workspace_id, revision
    )
    return RetrievedChunks(ranked, collection=_collection_name(user_id, workspace_id), revision=revision)
