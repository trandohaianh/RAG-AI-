"""Gemini streaming chain per-user."""
import asyncio
import threading
import logging
from concurrent.futures import TimeoutError as FutureTimeout
from collections.abc import AsyncIterator
from typing import Any

from costs.context import bind_context
from rag.llm import generate_text, stream_text
from rag.prompt import build_gemini_prompt, format_context
from rag.retriever import retrieve
from rag.evidence import capture_evidence, evidence_context, audit_citations

logger = logging.getLogger(__name__)
RAG_ERROR_MESSAGE = "Không thể hoàn tất câu trả lời RAG. Vui lòng thử lại."
_STREAM_BUFFER_SIZE = 16
_STREAM_CHUNK_CHARS = 512
_STREAM_SLOTS = threading.BoundedSemaphore(8)


class RagGenerationError(RuntimeError):
    """Public-safe generation failure; no provider details in the response."""

    def __init__(self):
        super().__init__(RAG_ERROR_MESSAGE)


def _unique_sources(chunks: list[tuple[str, str, int | None]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for _, source, _ in chunks:
        if source not in seen:
            seen.add(source)
            out.append(source)
    return out


def _iter_tokens(prompt: str):
    yield from stream_text(prompt)


_NO_DOC_MSG = (
    "Tôi chỉ trả lời dựa trên tài liệu đã tải lên (PDF, DOCX, TXT, ảnh), "
    "không trả lời câu hỏi kiến thức chung.\n\n"
    "- Chưa có tài liệu hoặc chưa lập chỉ mục: vào mục Tài liệu, tải file lên rồi hỏi lại.\n"
    "- Đã có tài liệu: hỏi đúng nội dung trong file (kể cả chữ trong ảnh)."
)


async def stream_rag_answer(
    user_id: int,
    question: str,
    history: list[dict[str, Any]] | None = None,
    source: str | None = None,
    *, include_evidence: bool = False,
) -> AsyncIterator[tuple[str, str | list[str] | None]]:
    chunks = await retrieve(user_id, question, source=source)

    try:
        from metrics import CHUNKS_RETRIEVED

        CHUNKS_RETRIEVED.observe(len(chunks))
    except Exception:
        pass

    if not chunks:
        yield ("token", _NO_DOC_MSG)
        if include_evidence:
            bundle = capture_evidence(chunks)
            bundle['citation_audit'] = audit_citations(_NO_DOC_MSG, bundle)
            yield "evidence", bundle
        yield ("done", [])
        return

    bundle = capture_evidence(chunks)
    context = evidence_context(bundle) if include_evidence else format_context(chunks)
    sources = _unique_sources(chunks)
    prompt = build_gemini_prompt(context, question, history)

    yield ("context", context)

    if not _STREAM_SLOTS.acquire(blocking=False):
        raise RagGenerationError()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=_STREAM_BUFFER_SIZE)
    stopped = threading.Event()

    def publish(event) -> bool:
        # Exactly one pending put per worker. Retrying a timed-out put would
        # duplicate tokens; keep waiting on the same Future instead.
        if stopped.is_set():
            return False
        put = queue.put(event)
        try:
            pending = asyncio.run_coroutine_threadsafe(put, loop)
        except RuntimeError:
            put.close()
            return False
        try:
            while not stopped.is_set():
                try:
                    pending.result(timeout=0.05)
                    return True
                except FutureTimeout:
                    continue
            return False
        except Exception:
            return False
        finally:
            if not pending.done():
                pending.cancel()

    def producer() -> None:
        iterator = None
        try:
            iterator = iter(_iter_tokens(prompt))
            has_text = False
            while not stopped.is_set():
                try:
                    token = next(iterator)
                except StopIteration:
                    break
                if stopped.is_set():
                    return
                if not isinstance(token, str):
                    raise TypeError("Invalid model chunk")
                has_text = has_text or bool(token.strip())
                for start in range(0, len(token), _STREAM_CHUNK_CHARS):
                    if not publish(("token", token[start:start + _STREAM_CHUNK_CHARS])):
                        return
            if not stopped.is_set():
                if not has_text:
                    raise ValueError("Empty model response")
                publish(("done", sources))
        except Exception as exc:
            logger.warning("RAG generation failed (%s)", type(exc).__name__)
            publish(("error", None))
        finally:
            # Only the owner thread may close a running synchronous generator.
            try:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()
            except Exception as exc:
                logger.warning("RAG stream cleanup failed (%s)", type(exc).__name__)
            finally:
                _STREAM_SLOTS.release()

    try:
        # bind_context: thread mới không tự kế thừa contextvars (user/feature để tính chi phí).
        threading.Thread(target=bind_context(producer), daemon=True, name="rag-generation").start()
    except BaseException:
        _STREAM_SLOTS.release()
        raise
    answer_parts = []
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "error":
                raise RagGenerationError()
            if include_evidence:
                if kind == "token":
                    answer_parts.append(payload)
                elif kind == "done":
                    bundle['citation_audit'] = audit_citations("".join(answer_parts), bundle)
                    if bundle['citation_audit']['status'] == "invalid":
                        raise RagGenerationError()
                    yield "evidence", bundle
            yield kind, payload
            if kind == "done":
                return
    finally:
        # Do not join here: a provider may still be in a synchronous socket read.
        # It retains its worker slot until read/cleanup actually finishes.
        stopped.set()


async def answer_rag_once(
    user_id: int,
    question: str,
    history: list[dict[str, Any]] | None = None,
    workspace_id: int | None = None,
    *, evidence_out: dict | None = None,
) -> tuple[str, list[str]]:
    """Trả lời RAG một lần (không stream) — cho Public API."""
    chunks = await retrieve(user_id, question, workspace_id=workspace_id)
    if not chunks:
        if evidence_out is not None:
            bundle = capture_evidence(chunks)
            bundle['citation_audit'] = audit_citations(_NO_DOC_MSG, bundle)
            evidence_out.update(bundle)
        return _NO_DOC_MSG, []

    bundle = capture_evidence(chunks)
    context = evidence_context(bundle) if evidence_out is not None else format_context(chunks)
    sources = _unique_sources(chunks)
    prompt = build_gemini_prompt(context, question, history)

    loop = asyncio.get_running_loop()

    try:
        text = await loop.run_in_executor(None, bind_context(generate_text), prompt)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Empty model response")
    except Exception as exc:
        logger.warning("RAG generation failed (%s)", type(exc).__name__)
        raise RagGenerationError() from None
    if evidence_out is not None:
        bundle['citation_audit'] = audit_citations(text, bundle)
        if bundle['citation_audit']['status'] == "invalid":
            raise RagGenerationError()
        evidence_out.update(bundle)
    return text, sources
