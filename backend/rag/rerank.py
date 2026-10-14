"""Xếp lại các chunk đã truy xuất theo mức độ liên quan với câu hỏi.

Retriever lấy về RETRIEVE_CANDIDATES chunk rồi gọi hàm này để giữ lại TOP_K
chunk sát nhất. Đường chính dùng Cohere Rerank. Khi không có API key, hoặc khi
gọi Cohere hỏng, bước xếp lại vẫn phải chạy — bằng bản chấm theo độ trùng từ ở
dưới — chứ không được bỏ qua.

Vì sao: trả về ``chunks[:top_n]`` nghĩa là giữ nguyên thứ tự của bước tìm theo
vector. Hệ thống vẫn trả lời, vẫn có dòng nguồn, vẫn trông bình thường, nhưng
câu hỏi "đèn LOS đỏ" lấy phải mục an toàn lao động thay vì mục xử lý sự cố.
Lỗi im lặng kiểu đó nguy hiểm hơn lỗi báo đỏ, nên không có nhánh nào ở đây được
phép bỏ qua việc xếp lại.
"""
from __future__ import annotations

import re

import httpx

from config import COHERE_API_KEY, COHERE_RERANK_MODEL, TOP_K

Chunk = tuple[str, str, int | None]

_WORD = re.compile(r"[0-9a-zA-ZÀ-ỹà-ỹ]+", re.UNICODE)
# Hư từ tiếng Việt: có mặt trong mọi câu nên không phân biệt được chunk nào hợp hơn.
_STOP = {"thì", "là", "và", "của", "có", "các", "cho", "khi", "phải", "gì", "nào",
         "trước", "sau", "này", "đó", "một", "được", "với", "trong", "ở", "ra"}
# Dòng tiêu đề mục kiểu "4. XỬ LÝ KHI ĐÈN LOS ĐỎ" — số thứ tự rồi chữ in hoa.
_HEADING = re.compile(r"^\d+\.\s+[^a-zà-ỹ]")
# Ca hiện trường gửi cả bản bàn giao làm query; câu hỏi của kỹ thuật viên nằm sau nhãn này.
_ASK_MARKER = "Câu hỏi kỹ thuật viên:"


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if w.lower() not in _STOP}


def _section_title(text: str) -> str:
    """Dòng tiêu đề mục mà chunk này nằm dưới, nếu có."""
    for line in (ln.strip() for ln in text.splitlines()):
        if _HEADING.match(line):
            return line
    return ""


def lexical_rerank(query: str, chunks: list[Chunk], top_n: int) -> list[Chunk]:
    """Xếp lại theo độ trùng từ giữa câu hỏi và chunk.

    Tiêu đề mục được tính nặng hơn thân bài vì nó nói thẳng mục đó để làm gì.
    Khi query là cả bản bàn giao ca, phần câu hỏi của kỹ thuật viên mang trọng
    số chính, còn bối cảnh xung quanh chỉ dùng để phá thế hoà — nếu chấm đều thì
    chữ nghĩa bàn giao lấn át đúng câu cần trả lời.
    """
    before, marker, tail = query.rpartition(_ASK_MARKER)
    primary = _tokens(tail if marker else query)
    context = _tokens(before) if marker else set()
    context -= primary
    if not primary:
        return chunks[:top_n]

    def score(chunk: Chunk) -> int:
        text = chunk[0]
        head = _tokens(_section_title(text))
        body = _tokens(text)
        return (4 * len(primary & head) + 2 * len(primary & body)
                + len(context & head))

    # sorted ổn định: chunk điểm bằng nhau giữ nguyên thứ tự của bước tìm vector.
    return sorted(chunks, key=score, reverse=True)[:top_n]


def rerank_chunks(
    query: str,
    chunks: list[Chunk],
    top_n: int | None = None,
) -> list[Chunk]:
    """chunks: (text, source, page). Trả về top_n chunk sau khi xếp lại."""
    top_n = top_n or TOP_K
    if not chunks:
        return []
    if len(chunks) <= top_n:
        return chunks[:top_n]
    if not COHERE_API_KEY:
        return lexical_rerank(query, chunks, top_n)

    documents = [c[0][:8000] for c in chunks]  # giới hạn Cohere
    try:
        resp = httpx.post(
            "https://api.cohere.com/v1/rerank",
            headers={
                "Authorization": f"Bearer {COHERE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": COHERE_RERANK_MODEL,
                "query": query[:2000],
                "documents": documents,
                "top_n": min(top_n, len(chunks)),
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        out: list[Chunk] = []
        for item in results:
            idx = item.get("index")
            if idx is None or idx < 0 or idx >= len(chunks):
                continue
            out.append(chunks[idx])
        return out if out else lexical_rerank(query, chunks, top_n)
    except Exception:
        # Cohere hỏng hoặc quá hạn: vẫn xếp lại tại chỗ, không trả về thứ tự thô.
        return lexical_rerank(query, chunks, top_n)
