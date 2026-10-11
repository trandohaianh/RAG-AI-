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
