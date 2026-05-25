"""
Retriever: vector search → optional Redis cache → Cohere rerank (tuần 1–2).
"""
import asyncio

from config import RETRIEVE_CANDIDATES, TOP_K
from rag.cache import get_cached_chunks, set_cached_chunks
from rag.ingest import get_vectorstore
from rag.rerank import rerank_chunks


def _search_sync(user_id: int, query: str, k: int) -> list[tuple[str, str, int | None]]:
    store = get_vectorstore(user_id)
    docs = store.similarity_search(query, k=k)
    results: list[tuple[str, str, int | None]] = []
    for doc in docs:
        source = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page")
        page_out = int(page) + 1 if page is not None else None
        results.append((doc.page_content, source, page_out))
    return results


async def retrieve(
    user_id: int, query: str, k: int | None = None
) -> list[tuple[str, str, int | None]]:
    final_k = k or TOP_K
    candidates = max(RETRIEVE_CANDIDATES, final_k)

    cached = get_cached_chunks(user_id, query, final_k)
    if cached is not None:
        return cached[:final_k]

    raw = await asyncio.to_thread(_search_sync, user_id, query, candidates)
    ranked = rerank_chunks(query, raw, top_n=final_k)
    set_cached_chunks(user_id, query, final_k, ranked)
    return ranked
