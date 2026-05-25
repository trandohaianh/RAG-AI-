"""Xây dựng prompt Gemini với context + lịch sử."""
from typing import Any

SYSTEM_PROMPT = (
    "Bạn là trợ lý RAG — chỉ trả lời dựa trên đoạn tài liệu trong Context. "
    "Không dùng kiến thức bên ngoài (Wikipedia, sách giáo khoa, v.v.). "
    "Nếu Context không chứa câu trả lời, hãy trả lời ngắn gọn bằng tiếng Việt: "
    "thông tin không có trong tài liệu đã upload; gợi ý hỏi lại theo nội dung file "
    "hoặc upload thêm tài liệu liên quan. Không bịa đặt."
)


def format_context(chunks: list[tuple[str, str, int | None]]) -> str:
    if not chunks:
        return "(Chưa có tài liệu — hãy upload trước.)"

    parts: list[str] = []
    for i, (text, source, page) in enumerate(chunks, 1):
        page_str = f", trang {page}" if page else ""
        parts.append(f"[{i}] Nguồn: {source}{page_str}\n{text}")
    return "\n\n---\n\n".join(parts)


def format_history(history: list[dict[str, Any]] | None) -> str:
    if not history:
        return ""
    lines = []
    for turn in history:
        label = "Người dùng" if turn.get("role") == "user" else "Trợ lý"
        lines.append(f"{label}: {turn.get('content', '')}")
    return "\n".join(lines)


def build_gemini_prompt(
    context: str,
    question: str,
    history: list[dict[str, Any]] | None = None,
) -> str:
    history_text = format_history(history)
    history_block = f"\n\nLịch sử hội thoại:\n{history_text}" if history_text else ""
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"Context tài liệu:\n{context}"
        f"{history_block}\n\n"
        f"Câu hỏi: {question}\n\n"
        f"Trả lời (tiếng Việt):"
    )
