"""
Retriever lai: (vector search ‖ BM25) → hợp nhất RRF → cache Redis → rerank.

BM25 chạy SONG SONG với tìm vector ở pha truy xuất, không phải ở pha xếp hạng lại.
Lý do: `rag.rerank.lexical_rerank` chỉ xếp lại nhóm ứng viên đã lấy về, nên một đoạn
chứa đúng mã lỗi mà tìm vector bỏ sót thì không có mặt để mà xếp. Xem `rag/bm25.py`.
"""
import asyncio
import logging

from config import RETRIEVE_CANDIDATES, TOP_K
from rag.bm25 import bm25_search, reciprocal_rank_fusion
from rag.cache import get_cached_chunks, set_cached_chunks
from rag.ingest import _collection_name, get_vectorstore
from rag.rerank import rerank_chunks

logger = logging.getLogger(__name__)


def _vector_search_sync(
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
        results.append((doc.page_content, source_out, page_out))
    return results


# Giữ tên cũ cho mã và bài kiểm thử đang gọi _search_sync.
_search_sync = _vector_search_sync


def _bm25_search_sync(
    user_id: int,
    query: str,
    k: int,
    source: str | None = None,
    workspace_id: int | None = None,
) -> list[tuple[str, str, int | None]]:
    """Nhánh từ khoá. Mọi lỗi đều nuốt và trả rỗng — tìm vector vẫn phải chạy được."""
    try:
        store = get_vectorstore(user_id, workspace_id)
        collection = getattr(store, "_collection", None)
        if collection is None:
            return []
        return bm25_search(
            collection, _collection_name(user_id, workspace_id), query, k, source
        )
    except Exception as exc:
        logger.warning("Nhánh BM25 bỏ qua: %s", exc)
        return []


def _hybrid_search_sync(
    user_id: int,
    query: str,
    k: int,
    source: str | None = None,
    workspace_id: int | None = None,
) -> list[tuple[str, str, int | None]]:
    """Chạy cả hai nhánh rồi hợp nhất bằng RRF; nếu BM25 rỗng thì giữ nguyên thứ tự vector."""
    vector_hits = _vector_search_sync(user_id, query, k, source, workspace_id)
    lexical_hits = _bm25_search_sync(user_id, query, k, source, workspace_id)
    if not lexical_hits:
        return vector_hits
    if not vector_hits:
        return lexical_hits
    return reciprocal_rank_fusion([vector_hits, lexical_hits], k=k)


async def retrieve(
    user_id: int,
    query: str,
    k: int | None = None,
    source: str | None = None,
    workspace_id: int | None = None,
) -> list[tuple[str, str, int | None]]:
    """source: tên file cụ thể để chỉ tìm trong 1 tài liệu — None = tìm trên toàn bộ kho."""
    final_k = k or TOP_K
    candidates = max(RETRIEVE_CANDIDATES, final_k)

    cached = get_cached_chunks(user_id, query, final_k, source, workspace_id)
    if cached is not None:
        return cached[:final_k]

    raw = await asyncio.to_thread(
        _hybrid_search_sync, user_id, query, candidates, source, workspace_id
    )
    ranked = rerank_chunks(query, raw, top_n=final_k)
    set_cached_chunks(user_id, query, final_k, ranked, source, workspace_id)
    return ranked
