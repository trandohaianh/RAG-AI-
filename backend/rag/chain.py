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
# Hai giới hạn tách nhau: _STREAM_SLOTS là số luồng trả lời ĐANG phục vụ client (trả lại ngay khi
# client ngắt), _STREAM_THREADS là số luồng provider còn sống (một luồng có thể kẹt trong lệnh đọc
# socket tới LLM_REQUEST_TIMEOUT_SEC sau khi client đã đi). Trước đây chỉ có một semaphore do luồng
# provider giữ: 8 client ngắt giữa chừng làm RAG từ chối mọi người trên worker đó tới 60 s.
_STREAM_SLOTS = threading.BoundedSemaphore(8)
_STREAM_THREADS = threading.BoundedSemaphore(32)


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


_MAX_HELD_LABEL_CHARS = 64


def _safe_release_point(text: str, released: int) -> int:
    """Vị trí xa nhất có thể gửi mà không cắt ngang một nhãn trích dẫn chưa đóng."""
    from rag.evidence import OPEN_LABEL_BRACKETS

    opening = max(text.rfind(ch, released) for ch in OPEN_LABEL_BRACKETS)
    if opening < 0:
        return len(text)
    tail = text[opening:]
    closed = "】" in tail or "］" in tail
    # Nhãn không chứa xuống dòng và rất ngắn; quá giới hạn thì đó không phải nhãn.
    if closed or "\n" in tail or len(tail) > _MAX_HELD_LABEL_CHARS:
        return len(text)
    return opening


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
    if not _STREAM_THREADS.acquire(blocking=False):
        _STREAM_SLOTS.release()
        raise RagGenerationError()
    slot = threading.Lock()   # trả _STREAM_SLOTS đúng một lần, dù consumer hay producer xong trước

    def release_slot() -> None:
        if slot.acquire(blocking=False):
            _STREAM_SLOTS.release()

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
                release_slot()
                _STREAM_THREADS.release()

    try:
        # bind_context: thread mới không tự kế thừa contextvars (user/feature để tính chi phí).
        threading.Thread(target=bind_context(producer), daemon=True, name="rag-generation").start()
    except BaseException:
        release_slot()
        _STREAM_THREADS.release()
        raise
    answer_parts = []
    pending = ""   # phần câu trả lời chưa gửi (chỉ là đuôi ngắn đang giữ một nhãn chưa đóng)
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "error":
                raise RagGenerationError()
            if include_evidence:
                if kind == "token":
                    # Không gửi một nhãn 【E#】 trước khi biết nó có thật: giữ lại phần từ ngoặc
                    # mở chưa đóng, và kiểm tra mọi nhãn đã đóng trước khi phát phần văn bản đó.
                    # Nhãn sai -> dừng ngay, người dùng không bao giờ thấy trích dẫn bịa.
                    # Chỉ kiểm phần mới (pending), không ghép và kiểm lại cả câu trả lời mỗi token:
                    # cách cũ tốn O(n²) với Ollama (mỗi token một sự kiện).
                    answer_parts.append(payload)
                    pending += payload
                    cutoff = _safe_release_point(pending, 0)
                    if cutoff > 0:
                        if audit_citations(pending[:cutoff], bundle)['status'] == "invalid":
                            raise RagGenerationError()
                        yield "token", pending[:cutoff]
                        pending = pending[cutoff:]
                    continue
                if kind == "done":
                    text = "".join(answer_parts)
                    bundle['citation_audit'] = audit_citations(text, bundle)
                    if bundle['citation_audit']['status'] == "invalid":
                        raise RagGenerationError()
                    if pending:
                        yield "token", pending
                    yield "evidence", bundle
            yield kind, payload
            if kind == "done":
                return
    finally:
        # Do not join here: a provider may still be in a synchronous socket read.
        # It keeps its _STREAM_THREADS permit until read/cleanup actually finishes, but the
        # client-facing slot is returned now.
        stopped.set()
        release_slot()


async def answer_rag_once(
    user_id: int,
    question: str,
    history: list[dict[str, Any]] | None = None,
    workspace_id: int | None = None,
    *, evidence_out: dict | None = None,
) -> tuple[str, list[str]]:
    """Trả lời RAG một lần (không stream) — cho Public API."""
    try:
        chunks = await retrieve(user_id, question, workspace_id=workspace_id)
    except Exception as exc:
        # Truy hồi lỗi (embedding/Chroma/BM25 cùng hỏng) phải thành lỗi công khai 503 như lỗi
        # sinh câu trả lời; trước đây nó lọt ra thành 500 trần ở /v1/ask và trợ lý ca.
        logger.warning("RAG retrieval failed (%s)", type(exc).__name__)
        raise RagGenerationError() from exc
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
