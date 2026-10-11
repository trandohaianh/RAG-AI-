"""Lớp gọi LLM dùng chung.

Sinh văn bản (chat, gợi ý câu hỏi, chấm điểm) chạy trên Gemini (SDK google-genai) hoặc
Ollama chạy local, chọn bằng LLM_PROVIDER. OCR ảnh luôn dùng Gemini vì cần model vision.
"""
import json
from collections.abc import Iterator
from functools import lru_cache

import httpx
from google import genai
from google.genai import types

from config import GEMINI_API_KEY, LLM_MODEL, LLM_PROVIDER, OLLAMA_MODEL, OLLAMA_URL

_OLLAMA_TIMEOUT = httpx.Timeout(300.0, connect=5.0)


@lru_cache(maxsize=1)
def client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise ValueError("Thiếu GEMINI_API_KEY")
    return genai.Client(api_key=GEMINI_API_KEY)


def _ollama_body(prompt: str, stream: bool, json_mode: bool, temperature, max_tokens) -> dict:
    options = {}
    if temperature is not None:
        options["temperature"] = temperature
    if max_tokens is not None:
        options["num_predict"] = max_tokens
    body = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": stream, "options": options}
    if json_mode:
        body["format"] = "json"
    return body


def generate_text(
    prompt: str,
    *,
    json_mode: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    if LLM_PROVIDER == "ollama":
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json=_ollama_body(prompt, False, json_mode, temperature, max_tokens),
            timeout=_OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")

    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
        response_mime_type="application/json" if json_mode else None,
    )
    resp = client().models.generate_content(model=LLM_MODEL, contents=prompt, config=config)
    return resp.text or ""


def stream_text(prompt: str) -> Iterator[str]:
    if LLM_PROVIDER == "ollama":
        with httpx.stream(
            "POST",
            f"{OLLAMA_URL}/api/generate",
            json=_ollama_body(prompt, True, False, None, None),
            timeout=_OLLAMA_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                piece = json.loads(line).get("response", "")
                if piece:
                    yield piece
        return

    for chunk in client().models.generate_content_stream(model=LLM_MODEL, contents=prompt):
        if chunk.text:
            yield chunk.text


def read_image_text(data: bytes, mime: str, prompt: str) -> str:
    """OCR qua Gemini multimodal (Ollama model nhỏ không đọc ảnh đủ tốt)."""
    resp = client().models.generate_content(
        model=LLM_MODEL,
        contents=[types.Part.from_bytes(data=data, mime_type=mime), prompt],
    )
    return resp.text or ""
