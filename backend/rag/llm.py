"""Client Gemini dùng chung (SDK google-genai, thay cho google-generativeai đã ngừng hỗ trợ)."""
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, LLM_MODEL


@lru_cache(maxsize=1)
def client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise ValueError("Thiếu GEMINI_API_KEY")
    return genai.Client(api_key=GEMINI_API_KEY)


def generate_text(contents: Any, config: types.GenerateContentConfig | None = None) -> str:
    resp = client().models.generate_content(model=LLM_MODEL, contents=contents, config=config)
    return resp.text or ""


def stream_text(contents: Any) -> Iterator[str]:
    for chunk in client().models.generate_content_stream(model=LLM_MODEL, contents=contents):
        if chunk.text:
            yield chunk.text
