"""Gợi ý câu hỏi từ tài liệu (tuần 3–4) — Gemini + sample chunks."""

from __future__ import annotations

import asyncio
import json
import re

from config import CHROMA_DIR, GEMINI_API_KEY
from rag.document_lifecycle import DocumentRecoveryError, collection_read_lock
from rag.ingest import _collection_name, get_vectorstore
from rag.llm import generate_text


def _sample_context_sync(user_id: int, max_chars: int = 6000) -> str:
    with collection_read_lock(CHROMA_DIR, _collection_name(user_id)):
        store = get_vectorstore(user_id)
        try:
            docs = store.similarity_search("nội dung chính tài liệu", k=10)
        except DocumentRecoveryError:
            raise
        except Exception:
            docs = []
    parts: list[str] = []
    total = 0
    for doc in docs:
        chunk = (doc.page_content or "")[:1500]
        if total + len(chunk) > max_chars:
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n---\n".join(parts)


async def suggest_questions_for_user(user_id: int, limit: int = 5) -> list[str]:
    if not GEMINI_API_KEY:
        return []

    context = await asyncio.to_thread(_sample_context_sync, user_id)
    if not context.strip():
        return []

    prompt = (
        f"Dưới đây là trích đoạn từ tài liệu của người dùng:\n\n{context}\n\n"
        f"Hãy đề xuất đúng {limit} câu hỏi ngắn (tiếng Việt) mà họ có thể hỏi chatbot RAG về nội dung này. "
        'Chỉ trả về một mảng JSON các chuỗi, ví dụ: ["Câu 1?", "Câu 2?"]. Không markdown.'
    )
    text = (await asyncio.to_thread(generate_text, prompt, feature="suggest")).strip()
    # Thử parse JSON
    try:
        m = re.search(r"\[[\s\S]*\]", text)
        if m:
            arr = json.loads(m.group())
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()][:limit]
    except Exception:
        pass
    lines = [ln.strip("- •").strip() for ln in text.splitlines() if ln.strip()]
    return [ln for ln in lines if "?" in ln or "？" in ln][:limit]
