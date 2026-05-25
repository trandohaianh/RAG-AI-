"""OCR ảnh/scan qua Gemini Vision (tuần 3–4)."""
from __future__ import annotations

import mimetypes
from pathlib import Path

import google.generativeai as genai

from config import GEMINI_API_KEY, LLM_MODEL


def extract_text_from_image_file(file_path: Path) -> str:
    """
    Đọc toàn bộ chữ trong ảnh (PNG/JPG/WebP/GIF) bằng Gemini multimodal.
    """
    if not GEMINI_API_KEY:
        raise ValueError("Thiếu GEMINI_API_KEY cho OCR")

    genai.configure(api_key=GEMINI_API_KEY)
    mime, _ = mimetypes.guess_type(str(file_path))
    mime = mime or "image/png"

    with open(file_path, "rb") as f:
        data = f.read()

    model = genai.GenerativeModel(LLM_MODEL)
    prompt = (
        "Bạn là OCR. Trích xuất TOÀN BỘ văn bản có trong ảnh, giữ nguyên thứ tự đọc được. "
        "Chỉ trả về nội dung chữ, không giải thích. Nếu không có chữ, trả về (trống)."
    )
    response = model.generate_content(
        [
            {"mime_type": mime, "data": data},
            prompt,
        ]
    )
    text = (response.text or "").strip()
    if not text or text == "(trống)":
        return ""
    return text
