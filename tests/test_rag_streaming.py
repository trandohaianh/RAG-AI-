"""RAG failures and cancellation must not become successful saved answers."""
import asyncio
import threading
import json
import socket
import time

import pytest

import rag.chain as chain
import rag.cache as cache


@pytest.fixture
def evidence(monkeypatch):
    async def retrieve(*args, **kwargs):
        return [("Reviewed evidence", "manual.pdf", 1)]
    monkeypatch.setattr(chain, "retrieve", retrieve)


def test_provider_failure_never_becomes_success(monkeypatch, evidence):
    def provider(prompt):
        yield "partial answer"
        raise RuntimeError("synthetic-secret-and-private-endpoint")
    monkeypatch.setattr(chain, "_iter_tokens", provider)
    events = []
    async def run():
        with pytest.raises(RuntimeError):
            async for event in chain.stream_rag_answer(1, "question"):
                events.append(event)
    asyncio.run(run())
    assert not any(kind == "done" for kind, _ in events)
    assert "synthetic-secret" not in str(events)


def test_cache_source_and_query_boundaries_are_distinct():
    first = cache.cache_key_retrieve(1, "revision:question", 5, source="manual.pdf")
    second = cache.cache_key_retrieve(1, "question", 5, source="manual.pdf:revision")
    assert first != second


@pytest.mark.parametrize("pieces", [[], ["", "  "]])
def test_empty_response_is_an_error(monkeypatch, evidence, pieces):
    monkeypatch.setattr(chain, "_iter_tokens", lambda prompt: iter(pieces))
    async def run():
        with pytest.raises(chain.RagGenerationError):
            async for _ in chain.stream_rag_answer(1, "question"):
                pass
    asyncio.run(run())


def test_slow_consumer_bounds_read_ahead_without_losing_or_duplicating_text(monkeypatch, evidence):
    produced = []
    pieces = [f"Đoạn {i}\n" for i in range(200)]
    def provider(prompt):
        for piece in pieces:
            produced.append(piece)
            yield piece
    monkeypatch.setattr(chain, "_iter_tokens", provider)
    async def run():
        stream = chain.stream_rag_answer(1, "question")
        assert (await anext(stream))[0] == "context"
        first = await anext(stream)
        await asyncio.sleep(0.15)
        assert 1 < len(produced) < 100  # bounded read-ahead, not the whole answer
        events = [first] + [event async for event in stream]
        assert "".join(payload for kind, payload in events if kind == "token") == "".join(pieces)
        assert [payload for kind, payload in events if kind == "done"] == [["manual.pdf"]]
    asyncio.run(asyncio.wait_for(run(), timeout=10))


def test_disconnect_with_full_buffer_closes_provider(monkeypatch, evidence):
    closed = threading.Event()
    def provider(prompt):
        try:
            for i in range(10000):
                yield str(i)
        finally:
            closed.set()
    monkeypatch.setattr(chain, "_iter_tokens", provider)
    async def run():
        stream = chain.stream_rag_answer(1, "question")
        await anext(stream)
        await anext(stream)
        await asyncio.sleep(0.15)
        await asyncio.wait_for(stream.aclose(), timeout=0.5)
        assert await asyncio.to_thread(closed.wait, 2)
    asyncio.run(run())


def test_disconnect_during_provider_read_keeps_capacity_until_worker_exits(monkeypatch, evidence):
    slots = threading.BoundedSemaphore(1)
    entered_read, release_read, closed = (threading.Event() for _ in range(3))
    def provider(prompt):
        try:
            yield "first"
            entered_read.set()
            release_read.wait(5)
            yield "late"
        finally:
            closed.set()
    monkeypatch.setattr(chain, "_STREAM_SLOTS", slots)
    monkeypatch.setattr(chain, "_iter_tokens", provider)
    async def run():
        stream = chain.stream_rag_answer(1, "question")
        try:
            await anext(stream)
            await anext(stream)
            assert await asyncio.to_thread(entered_read.wait, 2)
            await asyncio.wait_for(stream.aclose(), timeout=0.5)
            assert not closed.is_set()
            assert not slots.acquire(blocking=False)
            rejected = chain.stream_rag_answer(1, "another")
            await anext(rejected)
            with pytest.raises(chain.RagGenerationError):
                await anext(rejected)
        finally:
            release_read.set()
            await stream.aclose()
            assert await asyncio.to_thread(closed.wait, 2)
        deadline = asyncio.get_running_loop().time() + 2
        while not slots.acquire(blocking=False):
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        slots.release()
    asyncio.run(run())


@pytest.mark.parametrize("value", ["", None, RuntimeError("synthetic-private-provider-detail")])
def test_once_generation_failure_is_public_safe(monkeypatch, evidence, value):
    def generate(prompt):
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(chain, "generate_text", generate)
    with pytest.raises(chain.RagGenerationError) as caught:
        asyncio.run(chain.answer_rag_once(1, "question"))
    assert "synthetic-private" not in str(caught.value)


def test_missing_evidence_does_not_call_a_model(monkeypatch):
    async def retrieve(*args, **kwargs):
        return []
    def forbidden(*args, **kwargs):
        raise AssertionError("No model call should be made")
    monkeypatch.setattr(chain, "retrieve", retrieve)
    monkeypatch.setattr(chain, "_iter_tokens", forbidden)
    monkeypatch.setattr(chain, "generate_text", forbidden)
    async def run():
        events = [event async for event in chain.stream_rag_answer(1, "question")]
        assert events[-1] == ("done", [])
        text, sources = await chain.answer_rag_once(1, "question")
        assert text and sources == []
    asyncio.run(run())


def test_silent_redis_peer_falls_back_without_stalling_retrieval(monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3)
    stop = threading.Event()
    def peer():
        try:
            connection, _ = listener.accept()
            with connection:
                stop.wait(4)  # accept the connection but never answer Redis PING
        except OSError:
            pass
    worker = threading.Thread(target=peer, daemon=True)
    worker.start()
    monkeypatch.setattr(cache, "REDIS_URL", f"redis://127.0.0.1:{listener.getsockname()[1]}/0")
    monkeypatch.setattr(cache, "_client", None)
    try:
        start = time.monotonic()
        assert cache.get_cached_chunks(1, "question", 5) is None
        assert time.monotonic() - start < 2
    finally:
        stop.set()
        listener.close()
        worker.join(timeout=3)
