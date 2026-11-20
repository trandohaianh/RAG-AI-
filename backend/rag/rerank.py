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
import time
import unicodedata

import httpx

from config import COHERE_API_KEY, COHERE_RERANK_MODEL, TOP_K

Chunk = tuple[str, str, int | None]

_WORD = re.compile(r"[0-9a-zA-ZÀ-ỹà-ỹ]+", re.UNICODE)
# Hư từ tiếng Việt: có mặt trong mọi câu nên không phân biệt được chunk nào hợp hơn.
_STOP = {
    "thì",
    "là",
    "và",
    "của",
    "có",
    "các",
    "cho",
    "khi",
    "phải",
    "gì",
    "nào",
    "trước",
    "sau",
    "này",
    "đó",
    "một",
    "được",
    "với",
    "trong",
    "ở",
    "ra",
}
# Dòng tiêu đề mục kiểu "4. XỬ LÝ KHI ĐÈN LOS ĐỎ" — số thứ tự rồi chữ in hoa.
_HEADING = re.compile(r"^\d+\.\s+[^a-zà-ỹ]")
# Ca hiện trường gửi cả bản bàn giao làm query; câu hỏi của kỹ thuật viên nằm sau nhãn này.
_ASK_MARKER = "Câu hỏi kỹ thuật viên:"


def _tokens(text: str) -> set[str]:
    # NFC: dạng tổ hợp (NFD) nằm ngoài dải À-ỹ nên từ có dấu bị cắt vụn.
    return {w.lower() for w in _WORD.findall(unicodedata.normalize("NFC", text)) if w.lower() not in _STOP}


def _section_title(text: str) -> str:
    """Dòng tiêu đề mục mà chunk này nằm dưới, nếu có."""
    for line in (ln.strip() for ln in text.splitlines()):
        if _HEADING.match(line):
            return line
    return ""


class HybridRanking(list):
    """Danh sách chunk đã được xếp bằng RRF(BM25 + vector) — đã chứa tín hiệu từ vựng."""


def lexical_rerank(query: str, chunks: list[Chunk], top_n: int, keep_order: bool = False) -> list[Chunk]:
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
        return 4 * len(primary & head) + 2 * len(primary & body) + len(context & head)

    if not keep_order:
        # Đầu vào chỉ có MỘT tín hiệu (vector, khi nhánh BM25 hỏng/không có): độ trùng từ là tín
        # hiệu từ vựng duy nhất nên được xếp lại toàn bộ như trước. sorted ổn định: hoà thì giữ thứ
        # tự vector.
        return sorted(chunks, key=score, reverse=True)[:top_n]
    # Đầu vào là RRF (BM25 + vector): đã có tín hiệu từ vựng LẪN ngữ nghĩa. Trước đây hàm này vẫn
    # xếp lại hoàn toàn theo độ trùng từ, vứt bỏ tín hiệu vector. Giờ gộp hai thứ hạng bằng RRF
    # (k=60): thứ tự hybrid là tín hiệu chính, độ trùng từ chỉ đẩy lên/xuống và phá thế hoà; hoà
    # điểm thì giữ thứ tự hybrid (chủ sản phẩm chọn 2026-11-17).
    scores = [score(chunk) for chunk in chunks]
    lexical_rank = {i: r for r, i in enumerate(sorted(range(len(chunks)), key=lambda i: -scores[i]), 1)}
    fused = sorted(range(len(chunks)), key=lambda i: (-(1 / (60 + i + 1) + 1 / (60 + lexical_rank[i])), i))
    return [chunks[i] for i in fused[:top_n]]


def _record_rerank(data: dict, doc_count: int, started: float) -> None:
    """Cohere tính tiền theo lượt search (mỗi lượt ≤100 tài liệu)."""
    from costs.recorder import record_usage

    billed = ((data.get("meta") or {}).get("billed_units") or {}).get("search_units")
    units = float(billed) if isinstance(billed, (int, float)) else float(max(1, -(-doc_count // 100)))
    record_usage(
        provider="cohere",
        model=COHERE_RERANK_MODEL,
        units=units,
        estimated=not isinstance(billed, (int, float)),
        latency_ms=int((time.perf_counter() - started) * 1000),
        feature="rerank",
    )


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
    hybrid = isinstance(chunks, HybridRanking)
    if not COHERE_API_KEY:
        return lexical_rerank(query, chunks, top_n, keep_order=hybrid)

    documents = [c[0][:8000] for c in chunks]  # giới hạn Cohere
    started = time.perf_counter()
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
        _record_rerank(data, len(documents), started)
        results = data.get("results") or []
        out: list[Chunk] = []
        for item in results:
            idx = item.get("index")
            if idx is None or idx < 0 or idx >= len(chunks):
                continue
            out.append(chunks[idx])
        return out if out else lexical_rerank(query, chunks, top_n, keep_order=hybrid)
    except Exception:
        # Cohere hỏng hoặc quá hạn: vẫn xếp lại tại chỗ, không trả về thứ tự thô.
        return lexical_rerank(query, chunks, top_n, keep_order=hybrid)
