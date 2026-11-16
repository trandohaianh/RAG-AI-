import pytest
from types import SimpleNamespace
from google.genai import types
import json

import rag.llm as llm


class _FakeResponse:
    def __init__(self, payload=None, lines=None):
        self._payload = payload
        self._lines = lines or []

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_ollama_generate_sends_json_mode_and_options(monkeypatch):
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(url=url, body=json)
        return _FakeResponse({"response": '{"faithfulness": 1, "relevancy": 1}'})

    monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm.httpx, "post", fake_post)

    text = llm.generate_text("chấm điểm", json_mode=True, temperature=0.0, max_tokens=64)

    assert text == '{"faithfulness": 1, "relevancy": 1}'
    assert sent["url"].endswith("/api/generate")
    assert sent["body"]["format"] == "json"
    assert sent["body"]["stream"] is False
    assert sent["body"]["options"] == {"temperature": 0.0, "num_predict": 64}


def test_ollama_stream_yields_non_empty_pieces(monkeypatch):
    lines = [json.dumps({"response": "Hà"}), "", json.dumps({"response": " Nội"}),
             json.dumps({"response": "", "done": True})]

    monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm.httpx, "stream", lambda *a, **k: _FakeResponse(lines=lines))

    assert list(llm.stream_text("thủ đô?")) == ["Hà", " Nội"]


@pytest.mark.parametrize("lines", [
    [{"response": "partial"}, {"error": "synthetic-private-error"}],
    [{"response": "partial"}],
    [{"response": "partial"}, {"response": 42, "done": True}],
])
def test_ollama_stream_failure_or_missing_completion_is_not_success(monkeypatch, lines):
    monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm.httpx, "stream", lambda *a, **k: _FakeResponse(lines=[json.dumps(v) for v in lines]))
    with pytest.raises(RuntimeError) as caught:
        list(llm.stream_text("question"))
    assert "synthetic-private-error" not in str(caught.value)


def test_ollama_terminal_frame_can_contain_last_text(monkeypatch):
    monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm.httpx, "stream", lambda *a, **k: _FakeResponse(lines=[json.dumps({"response": "final", "done": True})]))
    assert list(llm.stream_text("question")) == ["final"]


def _gemini_chunk(text, reason=None):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(parts=[types.Part(text=text)]), finish_reason=reason,
    )])


@pytest.mark.parametrize("reason", ["STOP", "MAX_TOKENS", "SAFETY", None])
def test_gemini_requires_normal_completion_and_closes_stream(monkeypatch, reason):
    closed = []
    def provider():
        try:
            yield _gemini_chunk("partial")
            if reason is not None:
                yield _gemini_chunk("final", reason)
        finally:
            closed.append(True)
    monkeypatch.setattr(llm, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm, "client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content_stream=lambda **kwargs: provider())))
    if reason == "STOP":
        assert list(llm.stream_text("question")) == ["partial", "final"]
    else:
        with pytest.raises(RuntimeError):
            list(llm.stream_text("question"))
    assert closed == [True]


def test_closing_gemini_consumer_closes_request_stream(monkeypatch):
    closed = []
    def provider():
        try:
            yield _gemini_chunk("first")
            yield _gemini_chunk("second", "STOP")
        finally:
            closed.append(True)
    monkeypatch.setattr(llm, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm, "client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content_stream=lambda **kwargs: provider())))
    stream = llm.stream_text("question")
    assert next(stream) == "first"
    stream.close()
    assert closed == [True]



def test_gemini_usage_frame_after_stop_preserves_completion(monkeypatch):
    def provider():
        yield _gemini_chunk("answer", "STOP")
        yield types.GenerateContentResponse(usage_metadata=types.GenerateContentResponseUsageMetadata(total_token_count=5))
    monkeypatch.setattr(llm, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm, "client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content_stream=lambda **kwargs: provider())))
    assert list(llm.stream_text("question")) == ["answer"]
