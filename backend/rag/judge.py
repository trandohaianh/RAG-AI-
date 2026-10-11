"""Đánh giá tự động chất lượng câu trả lời RAG (LLM-as-judge).

Sau khi trả lời xong, gọi lại Gemini với vai trò "giám khảo" để chấm 2 điểm:
- faithfulness: câu trả lời có bám sát đúng Context đã retrieve hay tự bịa thêm.
- relevancy: câu trả lời có thực sự trả lời đúng trọng tâm câu hỏi hay không.

Chạy nền, không chặn luồng trả lời chính; lỗi bị nuốt (best-effort).
"""
import json
import re

from google.genai import types

from rag.llm import generate_text

_JUDGE_PROMPT = """Bạn là giám khảo chấm chất lượng câu trả lời của một chatbot RAG.
Chấm 2 tiêu chí, mỗi tiêu chí điểm số từ 0.0 đến 1.0 (số thực):

- faithfulness: câu trả lời có bám sát đúng nội dung trong phần Context bên dưới không,
  hay có bịa thêm thông tin không có trong Context. 1.0 = hoàn toàn bám sát nguồn,
  0.0 = bịa đặt hoàn toàn không dựa trên Context.
- relevancy: câu trả lời có thực sự trả lời đúng trọng tâm Câu hỏi không.
  1.0 = trả lời trúng và đầy đủ, 0.0 = lạc đề/không liên quan.

Nếu Context rỗng hoặc câu trả lời là lời từ chối hợp lệ (báo không có tài liệu), vẫn chấm
faithfulness = 1.0 (không bịa) và relevancy theo mức độ phù hợp của lời từ chối đó.

Context:
{context}

Câu hỏi: {question}

Câu trả lời cần chấm: {answer}

CHỈ trả về đúng 1 dòng JSON, không thêm chữ nào khác, theo đúng format:
{{"faithfulness": <số 0-1>, "relevancy": <số 0-1>}}"""

_JSON_RE = re.compile(r"\{[^{}]*\}")


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


async def judge_answer(question: str, context: str, answer: str) -> dict | None:
    """Gọi Gemini chấm điểm faithfulness/relevancy. Trả None nếu lỗi (best-effort)."""
    import asyncio

    prompt = _JUDGE_PROMPT.format(context=context[:6000], question=question, answer=answer[:3000])

    def _call() -> str:
        return generate_text(
            prompt,
            types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=1024,
                response_mime_type="application/json",
            ),
        )

    try:
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, _call)
        match = _JSON_RE.search(raw)
        if not match:
            print(f"[judge] không tìm thấy JSON trong phản hồi: {raw!r}")
            return None
        data = json.loads(match.group(0))
        faithfulness = _clamp01(float(data["faithfulness"]))
        relevancy = _clamp01(float(data["relevancy"]))
        return {"faithfulness": faithfulness, "relevancy": relevancy}
    except Exception as exc:
        print(f"[judge] lỗi chấm điểm: {exc!r}")
        return None
