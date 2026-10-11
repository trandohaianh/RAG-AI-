"""OCR ảnh/scan qua Gemini Vision (tuần 3–4)."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from google.genai import types

from config import GEMINI_API_KEY
from rag.llm import generate_text


def extract_text_from_image_file(file_path: Path) -> str:
    """
    Đọc toàn bộ chữ trong ảnh (PNG/JPG/WebP/GIF) bằng Gemini multimodal.
    """
    if not GEMINI_API_KEY:
        raise ValueError("Thiếu GEMINI_API_KEY cho OCR")

    mime, _ = mimetypes.guess_type(str(file_path))
    mime = mime or "image/png"

    with open(file_path, "rb") as f:
        data = f.read()

    prompt = (
        "Bạn là OCR. Trích xuất TOÀN BỘ văn bản có trong ảnh, giữ nguyên thứ tự đọc được. "
        "Chỉ trả về nội dung chữ, không giải thích. Nếu không có chữ, trả về (trống)."
    )
    text = generate_text([types.Part.from_bytes(data=data, mime_type=mime), prompt]).strip()
    if not text or text == "(trống)":
        return ""
    return text
