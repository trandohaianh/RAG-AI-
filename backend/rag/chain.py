"""Gemini streaming chain per-user."""
import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Any

import google.generativeai as genai

from config import GEMINI_API_KEY, LLM_MODEL
from rag.prompt import build_gemini_prompt, format_context
from rag.retriever import retrieve

genai.configure(api_key=GEMINI_API_KEY)


def _unique_sources(chunks: list[tuple[str, str, int | None]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for _, source, _ in chunks:
        if source not in seen:
            seen.add(source)
            out.append(source)
    return out


def _iter_tokens(prompt: str):
    model = genai.GenerativeModel(LLM_MODEL)
    response = model.generate_content(prompt, stream=True)
    for chunk in response:
        if chunk.text:
            yield chunk.text


_NO_DOC_MSG = (
    "Tôi chỉ trả lời dựa trên **tài liệu bạn đã upload** (PDF, DOCX, TXT, ảnh OCR), "
    "không phải câu hỏi kiến thức chung.\n\n"
    "• Chưa có tài liệu hoặc chưa lập chỉ mục → vào **Tài liệu**, upload file rồi hỏi lại.\n"
    "• Đã có tài liệu → hỏi đúng nội dung file (kể cả ảnh đã OCR)."
)


async def stream_rag_answer(
    user_id: int,
    question: str,
    history: list[dict[str, Any]] | None = None,
) -> AsyncIterator[tuple[str, str | list[str] | None]]:
    chunks = await retrieve(user_id, question)

    try:
        from metrics import CHUNKS_RETRIEVED

        CHUNKS_RETRIEVED.observe(len(chunks))
    except Exception:
        pass

    if not chunks:
        yield ("token", _NO_DOC_MSG)
        yield ("done", [])
        return

    context = format_context(chunks)
    sources = _unique_sources(chunks)
    prompt = build_gemini_prompt(context, question, history)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, str | list[str] | None] | None] = asyncio.Queue()

    def _producer() -> None:
        try:
            for token in _iter_tokens(prompt):
                asyncio.run_coroutine_threadsafe(
                    queue.put(("token", token)), loop
                ).result()
        except Exception as exc:
            asyncio.run_coroutine_threadsafe(
                queue.put(("token", f"[Lỗi: {exc}]")), loop
            ).result()
        finally:
            asyncio.run_coroutine_threadsafe(
                queue.put(("done", sources)), loop
            ).result()
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

    threading.Thread(target=_producer, daemon=True).start()

    while True:
        item = await queue.get()
        if item is None:
            break
        yield item


async def answer_rag_once(
    user_id: int,
    question: str,
    history: list[dict[str, Any]] | None = None,
) -> tuple[str, list[str]]:
    """Trả lời RAG một lần (không stream) — cho Public API."""
    chunks = await retrieve(user_id, question)
    if not chunks:
        return _NO_DOC_MSG, []

    context = format_context(chunks)
    sources = _unique_sources(chunks)
    prompt = build_gemini_prompt(context, question, history)

    loop = asyncio.get_running_loop()

    def _generate() -> str:
        model = genai.GenerativeModel(LLM_MODEL)
        resp = model.generate_content(prompt)
        return resp.text or ""

    text = await loop.run_in_executor(None, _generate)
    return text, sources
