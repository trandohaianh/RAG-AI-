"""Tests rerank: short-circuit, và bản dự phòng khi không có Cohere API key."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from rag.rerank import lexical_rerank, rerank_chunks

SAFETY = (
    "1. AN TOÀN LAO ĐỘNG\n"
    "Không leo cột khi trời mưa. Luôn đeo dây an toàn và ngắt điện trước khi thao tác.",
    "SOP-ATLD-002",
    1,
)
LOS = (
    "4. XỬ LÝ KHI ĐÈN LOS ĐỎ\n"
    "Tháo dây quang khỏi cổng PON. Lau đầu nối bằng cồn isopropyl.\n"
    "Cắm lại, chờ 30 giây rồi kiểm tra lại đèn LOS.",
    "SOP-GPON-014",
    4,
)
WIFI = ("7. WIFI CHẬM GIỜ CAO ĐIỂM\nĐổi kênh phát sóng.", "SOP-WIFI-003", 7)
PAD = [(f"{i}. MỤC KHÁC {i}\nNội dung không liên quan.", f"DOC-{i}", i) for i in range(10, 22)]

# Thứ tự của bước tìm theo vector: mục an toàn lao động đứng đầu, mục đúng nằm cuối.
RAW = [SAFETY, WIFI] + PAD + [LOS]


def test_rerank_short_circuit_no_api():
    """len(chunks) <= top_n → không gọi Cohere."""
    chunks = [("text a", "f1.pdf", 1), ("text b", "f1.pdf", 2)]
    out = rerank_chunks("query", chunks, top_n=5)
    assert len(out) == 2
    assert out[0][0] == "text a"


def test_rerank_khong_co_api_key_van_xep_lai(monkeypatch):
    """Không có API key thì vẫn phải xếp lại, không được trả về thứ tự thô.

    Đây là hồi quy của một lỗi im lặng: trước đây nhánh này trả về
    ``chunks[:top_n]``, nên "đèn LOS đỏ" lấy phải mục an toàn lao động.
    """
    monkeypatch.setattr("rag.rerank.COHERE_API_KEY", "", raising=False)
    q = ("Mất mạng hoàn toàn, modem có đèn đỏ. "
         "Câu hỏi kỹ thuật viên: đèn LOS đỏ thì kiểm tra gì trước?")
    out = rerank_chunks(q, RAW, top_n=5)
    assert len(out) == 5
    assert out[0][1] == "SOP-GPON-014"          # mục xử lý, không phải mục an toàn
    assert SAFETY not in out[:1]


def test_lexical_rerank_khong_co_nhan_cau_hoi():
    """Query là câu hỏi trần (không kèm bản bàn giao ca) vẫn xếp đúng."""
    out = lexical_rerank("đèn LOS đỏ kiểm tra gì", RAW, top_n=3)
    assert len(out) == 3
    assert out[0][1] == "SOP-GPON-014"


def test_lexical_rerank_truong_hop_bien():
    """Danh sách rỗng, query rỗng, và ít chunk hơn top_n đều không làm vỡ."""
    assert rerank_chunks("q", [], top_n=5) == []
    assert len(lexical_rerank("", RAW, top_n=5)) == 5          # rơi về thứ tự thô
    assert lexical_rerank("đèn LOS", [LOS], top_n=5) == [LOS]
