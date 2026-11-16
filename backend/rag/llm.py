"""Lớp gọi LLM dùng chung.

Sinh văn bản (chat, gợi ý câu hỏi, chấm điểm) chạy trên Gemini (SDK google-genai) hoặc
Ollama chạy local, chọn bằng LLM_PROVIDER. OCR ảnh luôn dùng Gemini vì cần model vision.
"""
import json
import time
from collections.abc import Iterator
from functools import lru_cache

import httpx
from google import genai
from google.genai import types

from config import GEMINI_API_KEY, LLM_MODEL, LLM_PROVIDER, LLM_REQUEST_TIMEOUT_SEC, OLLAMA_MODEL, OLLAMA_URL
from costs.recorder import estimate_tokens, gemini_usage, record_usage

_OLLAMA_TIMEOUT = httpx.Timeout(LLM_REQUEST_TIMEOUT_SEC, connect=min(5.0, LLM_REQUEST_TIMEOUT_SEC))


@lru_cache(maxsize=1)
def client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise ValueError("Thiếu GEMINI_API_KEY")
    return genai.Client(api_key=GEMINI_API_KEY, http_options=types.HttpOptions(timeout=int(LLM_REQUEST_TIMEOUT_SEC * 1000)))


def _ollama_body(prompt: str, stream: bool, json_mode: bool, temperature, max_tokens) -> dict:
    options = {}
    if temperature is not None:
        options["temperature"] = temperature
    if max_tokens is not None:
        options["num_predict"] = max_tokens
    body = {"model": OLLAMA_MODEL, "prompt": str(prompt), "stream": stream, "options": options}
    system = _system_of(prompt)
    if system:
        body["system"] = system
    if json_mode:
        body["format"] = "json"
    return body


def _system_of(prompt) -> str | None:
    """Quy tắc hệ thống đi kèm prompt (rag.prompt.RagPrompt), nếu có."""
    system = getattr(prompt, "system", None)
    return system if isinstance(system, str) and system else None


def _metered_text(prompt) -> str:
    """Văn bản dùng để ước lượng token đầu vào: gồm cả phần system."""
    system = _system_of(prompt)
    return f"{system}\n\n{prompt}" if system else str(prompt)


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _record_ollama(payload: dict, prompt: str, text: str, start: float, feature: str | None,
                   status: str = "ok") -> None:
    record_usage(
        provider="ollama", model=f"ollama:{OLLAMA_MODEL}",
        input_tokens=payload.get("prompt_eval_count") or estimate_tokens(prompt),
        output_tokens=payload.get("eval_count") or estimate_tokens(text),
        estimated=not payload.get("eval_count"), latency_ms=_elapsed_ms(start),
        status=status, feature=feature,
    )


def _record_gemini(response, prompt_text: str, text: str, start: float, feature: str | None,
                   status: str = "ok") -> None:
    inp, out, thinking = gemini_usage(response) if response is not None else (0, 0, 0)
    estimated = not (inp or out)
    if estimated:
        inp, out = estimate_tokens(prompt_text), estimate_tokens(text)
    elif not out and text:
        # Stream bị ngắt: chunk usage cuối mới có prompt_token_count, chưa có số token đầu ra,
        # nhưng văn bản đã sinh vẫn bị tính tiền.
        out, estimated = estimate_tokens(text), True
    record_usage(
        provider="gemini", model=LLM_MODEL, input_tokens=inp, output_tokens=out,
        thinking_tokens=thinking, estimated=estimated, latency_ms=_elapsed_ms(start),
        status=status, feature=feature,
    )


def generate_text(
    prompt: str,
    *,
    json_mode: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
    feature: str | None = None,
) -> str:
    start = time.perf_counter()
    if LLM_PROVIDER == "ollama":
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json=_ollama_body(prompt, False, json_mode, temperature, max_tokens),
            timeout=_OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict) or "error" in payload:
            raise RuntimeError("Ollama generation failed")
        text = payload.get("response", "")
        _record_ollama(payload, _metered_text(prompt), text, start, feature)
        return text

    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
        response_mime_type="application/json" if json_mode else None,
        system_instruction=_system_of(prompt),
    )
    try:
        resp = client().models.generate_content(model=LLM_MODEL, contents=str(prompt), config=config)
    except Exception:
        _record_gemini(None, "", "", start, feature, status="error")
        raise
    text = resp.text or ""
    _record_gemini(resp, _metered_text(prompt), text, start, feature)
    return text


def stream_text(prompt: str, *, feature: str | None = None) -> Iterator[str]:
    start = time.perf_counter()
    if LLM_PROVIDER == "ollama":
        produced: list[str] = []
        recorded = started = False
        try:
            with httpx.stream(
                "POST",
                f"{OLLAMA_URL}/api/generate",
                json=_ollama_body(prompt, True, False, None, None),
                timeout=_OLLAMA_TIMEOUT,
            ) as resp:
                resp.raise_for_status()
                started = True
                for line in resp.iter_lines():
                    if not line:
                        continue
                    payload = json.loads(line)
                    if not isinstance(payload, dict) or "error" in payload:
                        raise RuntimeError("Ollama stream failed")
                    piece = payload.get("response", "")
                    if not isinstance(piece, str):
                        raise RuntimeError("Invalid Ollama response")
                    if piece:
                        produced.append(piece)
                        yield piece
                    if payload.get("done") is True:
                        recorded = True
                        _record_ollama(payload, _metered_text(prompt), "".join(produced), start, feature)
                        return
                raise RuntimeError("Ollama stream ended before completion")
        finally:
            # Giống nhánh Gemini: client ngắt hoặc lỗi giữa stream vẫn phải để lại đúng một bản ghi.
            if started and not recorded:
                _record_ollama({}, _metered_text(prompt), "".join(produced), start, feature, status="partial")

    stream = client().models.generate_content_stream(
        model=LLM_MODEL, contents=str(prompt),
        config=types.GenerateContentConfig(system_instruction=_system_of(prompt)),
    )
    completed = False
    last_with_usage = None
    produced_chars: list[str] = []
    try:
        for chunk in stream:
            if getattr(chunk, "usage_metadata", None) is not None:
                # Gemini gửi usage tích luỹ; chunk cuối có số đầy đủ.
                last_with_usage = chunk
            for candidate in chunk.candidates or []:
                reason = candidate.finish_reason
                if reason is not None:
                    if getattr(reason, "value", reason) != "STOP":
                        raise RuntimeError("Gemini response did not complete normally")
                    completed = True
            if chunk.text:
                produced_chars.append(chunk.text)
                yield chunk.text
        if not completed:
            raise RuntimeError("Gemini stream ended before completion")
    finally:
        # Ghi cả khi client ngắt giữa chừng: token đã sinh vẫn bị tính tiền.
        _record_gemini(last_with_usage, _metered_text(prompt), "".join(produced_chars), start, feature,
                       status="ok" if completed else "partial")
        close = getattr(stream, "close", None)
        if close is not None:
            close()


def read_image_text(data: bytes, mime: str, prompt: str) -> str:
    """OCR qua Gemini multimodal (Ollama model nhỏ không đọc ảnh đủ tốt)."""
    start = time.perf_counter()
    resp = client().models.generate_content(
        model=LLM_MODEL,
        contents=[types.Part.from_bytes(data=data, mime_type=mime), prompt],
    )
    text = resp.text or ""
    # Ảnh ≈ 258 token/ô 768px theo tài liệu Gemini; chỉ dùng khi API không trả usage.
    _record_gemini(resp, prompt + " " * (258 * 4), text, start, "ocr")
    return text
