"""BM25 ở PHA TRUY XUẤT — bắt trùng khớp chính xác mà tìm theo vector bỏ sót.

Vì sao cần, và vì sao phải đặt ở đây chứ không phải ở pha xếp hạng lại:

Tìm theo vector hiểu NGHĨA — hỏi "máy không lên nguồn" vẫn tìm được đoạn viết
"thiết bị mất điện". Nhưng kỹ thuật viên hiện trường thường gõ đúng mã lỗi
("E-204") hoặc mã model thiết bị ("GPON-2412B"). Những chuỗi này bị bộ mã hoá
băm thành các mảnh token không mang nghĩa, nên vector của chúng không gần vector
của đoạn văn chứa đúng mã đó.

Dự án đã có `rag.rerank.lexical_rerank` chấm theo từ khoá, nhưng nó chỉ xếp lại
RETRIEVE_CANDIDATES đoạn ĐÃ lấy về. Nếu đoạn chứa "E-204" không lọt vào nhóm ứng
viên đó thì xếp lại bao nhiêu lần cũng vô ích — nó không có ở đó để mà xếp. Vì vậy
BM25 phải chạy ở pha truy xuất, song song với tìm theo vector, rồi hợp nhất kết quả.

Chi phí: không cần GPU, không cần mô hình, chỉ một chỉ mục ngược trong bộ nhớ.
"""
from __future__ import annotations

import logging
import re
import threading

logger = logging.getLogger(__name__)

try:
    from rank_bm25 import BM25Okapi

    BM25_AVAILABLE = True
except ImportError:  # pragma: no cover - môi trường chưa cài thư viện
    BM25Okapi = None
    BM25_AVAILABLE = False

# Giữ nguyên cụm có gạch nối/gạch chéo làm MỘT token: "e-204", "gpon-2412b", "24/7".
# `[^\W_]` là ký tự chữ-hoặc-số theo Unicode (giữ được dấu tiếng Việt) nhưng loại "_".
_TOKEN_RE = re.compile(r"[^\W_]+(?:[-/][^\W_]+)*", re.UNICODE)

# Chỉ mục BM25 theo từng collection, kèm khoá phiên bản để biết khi nào phải dựng lại.
_INDEX_CACHE: dict[str, tuple[int, object, list[tuple[str, str, int | None]]]] = {}
_CACHE_VERSION: dict[str, int] = {}
_LOCK = threading.Lock()


def tokenize(text: str) -> list[str]:
    """Tách token, giữ nguyên mã kỹ thuật và BỔ SUNG các phần con của chúng.

    "Lỗi E-204 trên GPON-2412B" -> ['lỗi', 'e-204', '204', 'gpon-2412b', 'gpon', '2412b']

    Phần con được thêm vào để câu hỏi chỉ gõ "204" vẫn chạm được đoạn viết "E-204",
    trong khi token đầy đủ "e-204" vẫn là token hiếm nên IDF của nó rất cao và đoạn
    chứa đúng mã vẫn được đẩy lên trên.

    Mảnh dài 1 ký tự bị loại: tiền tố một chữ cái như "e" trong "E-204" xuất hiện ở
    MỌI mã lỗi cùng họ, nên giữ lại sẽ khiến hỏi "E-999" lại khớp đoạn viết về
    "E-204". Token đầy đủ vẫn còn nên không mất khả năng tìm đúng mã.
    """
    tokens: list[str] = []
    for match in _TOKEN_RE.findall(text.lower()):
        tokens.append(match)
        if "-" in match or "/" in match:
            tokens.extend(part for part in re.split(r"[-/]", match) if len(part) > 1)
    return tokens


def invalidate(collection_name: str) -> None:
    """Đánh dấu chỉ mục của một collection là cũ — gọi sau khi nạp hoặc xoá tài liệu.

    Không xoá chỉ mục ngay mà chỉ tăng số phiên bản, để truy vấn đang chạy dở không
    bị mất chỉ mục giữa chừng; lần truy vấn kế tiếp sẽ tự dựng lại.
    """
    with _LOCK:
        _CACHE_VERSION[collection_name] = _CACHE_VERSION.get(collection_name, 0) + 1


def _build_index(collection) -> tuple[object, list[tuple[str, str, int | None]]]:
    """Đọc toàn bộ đoạn của collection và dựng chỉ mục BM25 trong bộ nhớ."""
    data = collection.get(include=["documents", "metadatas"])
    documents = data.get("documents") or []
    metadatas = data.get("metadatas") or []

    rows: list[tuple[str, str, int | None]] = []
    corpus: list[list[str]] = []
    for text, meta in zip(documents, metadatas):
        if not text:
            continue
        meta = meta or {}
        page = meta.get("page")
        rows.append((text, meta.get("source", "unknown"), int(page) + 1 if page is not None else None))
        corpus.append(tokenize(text))

    if not corpus:
        return None, []
    return BM25Okapi(corpus), rows


def _get_index(collection, collection_name: str):
    """Trả về (bm25, rows) từ bộ nhớ đệm, dựng lại khi số phiên bản đã đổi."""
    want = _CACHE_VERSION.get(collection_name, 0)
    cached = _INDEX_CACHE.get(collection_name)
    if cached is not None and cached[0] == want:
        return cached[1], cached[2]

    bm25, rows = _build_index(collection)
    with _LOCK:
        _INDEX_CACHE[collection_name] = (want, bm25, rows)
    return bm25, rows


def bm25_search(
    collection,
    collection_name: str,
    query: str,
    k: int,
    source: str | None = None,
) -> list[tuple[str, str, int | None]]:
    """Tìm theo từ khoá, trả về cùng dạng bộ ba (nội dung, nguồn, trang) như tìm vector.

    Trả về danh sách rỗng khi chưa cài rank_bm25 hoặc kho chưa có tài liệu — nơi gọi
    vẫn chạy bình thường với riêng kết quả vector.
    """
    if not BM25_AVAILABLE:
        return []
    try:
        bm25, rows = _get_index(collection, collection_name)
        if bm25 is None or not rows:
            return []

        tokens = tokenize(query)
        if not tokens:
            return []

        scores = bm25.get_scores(tokens)
        order = sorted(range(len(rows)), key=lambda i: scores[i], reverse=True)

        out: list[tuple[str, str, int | None]] = []
        for i in order:
            if scores[i] <= 0:
                break                      # hết đoạn có từ khoá trùng — không lấy nhiễu
            if source and rows[i][1] != source:
                continue
            out.append(rows[i])
            if len(out) >= k:
                break
        return out
    except Exception as exc:
        logger.warning("BM25 không chạy được, dùng riêng kết quả vector: %s", exc)
        return []


def reciprocal_rank_fusion(
    rankings: list[list[tuple[str, str, int | None]]],
    k: int,
    rrf_k: int = 60,
) -> list[tuple[str, str, int | None]]:
    """Hợp nhất nhiều bảng xếp hạng bằng Reciprocal Rank Fusion.

        score(d) = Σ  1 / (rrf_k + thứ_hạng_của_d_trong_bảng_i)

    Dùng RRF thay vì cộng điểm thô vì điểm cosine và điểm BM25 không cùng thang đo và
    không so sánh trực tiếp được; RRF chỉ dùng THỨ HẠNG nên không cần chuẩn hoá.
    Hằng rrf_k = 60 là giá trị chuẩn trong bài báo gốc, làm dịu ảnh hưởng của vài vị
    trí đầu để một bảng xếp hạng không áp đảo bảng còn lại.
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, tuple[str, str, int | None]] = {}

    for ranking in rankings:
        for rank, row in enumerate(ranking):
            key = f"{row[1]}||{row[2]}||{hash(row[0])}"
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank + 1)
            first_seen.setdefault(key, row)

    best = sorted(scores, key=lambda key: scores[key], reverse=True)
    return [first_seen[key] for key in best[:k]]
