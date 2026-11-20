"""BM25 ở pha truy xuất — bắt mã lỗi/mã model mà tìm theo vector bỏ sót.

Bài kiểm thử trọng tâm là `test_bm25_cuu_duoc_doan_vector_bo_sot`: nó dựng lại đúng
tình huống thực tế — đoạn chứa mã lỗi KHÔNG nằm trong nhóm ứng viên mà tìm vector
lấy về — và chứng minh rằng xếp hạng lại không cứu được, chỉ BM25 ở pha truy xuất
mới cứu được.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from rag.bm25 import (  # noqa: E402
    BM25_AVAILABLE,
    bm25_search,
    invalidate,
    reciprocal_rank_fusion,
    tokenize,
)

pytestmark = pytest.mark.skipif(not BM25_AVAILABLE, reason="chưa cài rank_bm25")


class FakeCollection:
    """Giả lập collection Chroma: chỉ cần .get() trả documents + metadatas."""

    def __init__(self, rows):
        self._rows = rows

    def get(self, include=None):
        return {
            "documents": [r[0] for r in self._rows],
            "metadatas": [{"source": r[1], "page": r[2]} for r in self._rows],
        }


DOCS = [
    ("Quy trình bảo trì định kỳ: kiểm tra nguồn, vệ sinh quạt, siết lại đầu nối.", "baotri.pdf", 0),
    ("Thiết bị mất điện đột ngột thì kiểm tra aptomat và dây nguồn đầu vào.", "nguon.pdf", 0),
    ("Mã lỗi E-204: lỗi đồng bộ quang, thay dây nhảy và khởi động lại ONU.", "maloi.pdf", 3),
    ("Model GPON-2412B hỗ trợ tối đa 24 cổng, nguồn 12V.", "thietbi.pdf", 7),
    ("Hướng dẫn chung về an toàn điện khi làm việc tại hiện trường.", "antoan.pdf", 1),
]


class TestTokenize:
    def test_giu_nguyen_ma_loi_co_gach_noi(self):
        assert "e-204" in tokenize("Mã lỗi E-204 xuất hiện")

    def test_bo_sung_phan_con_cua_ma(self):
        toks = tokenize("E-204")
        assert "e-204" in toks and "204" in toks

    def test_khong_sinh_manh_mot_ky_tu(self):
        """Giữ 'e' sẽ khiến hỏi E-999 lại khớp đoạn viết về E-204."""
        assert "e" not in tokenize("E-204")

    def test_giu_dau_tieng_viet(self):
        assert "lỗi" in tokenize("Mã lỗi")

    def test_ma_model_dai_van_la_mot_token(self):
        assert "gpon-2412b" in tokenize("Model GPON-2412B hỗ trợ 24 cổng")


class TestBm25Search:
    def test_tim_dung_doan_chua_ma_loi(self):
        invalidate("c1")
        hits = bm25_search(FakeCollection(DOCS), "c1", "E-204", k=3)
        assert hits, "phải tìm được ít nhất một đoạn"
        assert "E-204" in hits[0][0], f"đoạn đầu phải chứa mã lỗi, nhận được: {hits[0][0]!r}"
        assert hits[0][1] == "maloi.pdf"

    def test_tim_dung_doan_chua_ma_model(self):
        invalidate("c2")
        hits = bm25_search(FakeCollection(DOCS), "c2", "GPON-2412B", k=3)
        assert "GPON-2412B" in hits[0][0]

    def test_khong_tra_ve_doan_khong_co_tu_khoa_nao(self):
        invalidate("c3")
        hits = bm25_search(FakeCollection(DOCS), "c3", "E-204", k=99)
        assert len(hits) < len(DOCS), "phải dừng khi hết đoạn có từ khoá trùng, không trả nhiễu"

    def test_loc_theo_source(self):
        invalidate("c4")
        hits = bm25_search(FakeCollection(DOCS), "c4", "nguồn", k=5, source="nguon.pdf")
        assert all(h[1] == "nguon.pdf" for h in hits)

    def test_tra_ve_rong_khi_kho_trong(self):
        invalidate("c5")
        assert bm25_search(FakeCollection([]), "c5", "E-204", k=5) == []

    def test_tra_so_trang_dang_1_based(self):
        invalidate("c6")
        hits = bm25_search(FakeCollection(DOCS), "c6", "E-204", k=1)
        assert hits[0][2] == 4, "page 3 trong metadata phải thành trang 4 khi hiển thị"

    def test_dung_lai_chi_muc_sau_khi_invalidate(self):
        col = FakeCollection(list(DOCS))
        invalidate("c7")
        assert bm25_search(col, "c7", "E-999", k=3) == []

        col._rows = DOCS + [("Mã lỗi E-999: quá nhiệt nguồn.", "moi.pdf", 0)]
        invalidate("c7")
        hits = bm25_search(col, "c7", "E-999", k=3)
        assert hits and "E-999" in hits[0][0], "sau invalidate phải thấy tài liệu mới nạp"


class TestReciprocalRankFusion:
    def test_doan_xuat_hien_o_ca_hai_bang_duoc_day_len(self):
        chung = ("đoạn chung", "a.pdf", 1)
        vector = [("chỉ vector", "b.pdf", 1), chung]
        lexical = [("chỉ từ khoá", "c.pdf", 1), chung]
        out = reciprocal_rank_fusion([vector, lexical], k=3)
        assert out[0] == chung, "đoạn được cả hai nhánh tìm thấy phải xếp đầu"

    def test_khong_tra_ve_doan_trung_lap(self):
        row = ("x", "a.pdf", 1)
        out = reciprocal_rank_fusion([[row], [row]], k=5)
        assert len(out) == 1

    def test_ton_trong_gioi_han_k(self):
        rows = [(f"đoạn {i}", "a.pdf", i) for i in range(10)]
        assert len(reciprocal_rank_fusion([rows], k=4)) == 4

    def test_mot_bang_rong_khong_lam_hong_ket_qua(self):
        rows = [("x", "a.pdf", 1), ("y", "b.pdf", 2)]
        assert reciprocal_rank_fusion([rows, []], k=5) == rows


class TestVanDeGocDuocGiaiQuyet:
    def test_bm25_cuu_duoc_doan_vector_bo_sot(self):
        """Tái hiện đúng lỗi: đoạn chứa E-204 không nằm trong nhóm vector lấy về.

        Xếp hạng lại chỉ làm việc trên nhóm ứng viên, nên nó KHÔNG thể cứu. Chỉ có
        BM25 chạy ở pha truy xuất mới đưa được đoạn đó vào.
        """
        from rag.rerank import lexical_rerank

        # Dạng mà pha truy xuất trả ra: số trang đã đổi sang 1-based (3 -> 4).
        doan_dung = (DOCS[2][0], DOCS[2][1], DOCS[2][2] + 1)
        vector_tra_ve = [DOCS[0], DOCS[1], DOCS[4]]  # tìm theo nghĩa, trượt mã lỗi

        sau_khi_xep_lai = lexical_rerank("E-204", list(vector_tra_ve), top_n=3)
        assert doan_dung not in sau_khi_xep_lai, (
            "tiền đề của bài kiểm thử: xếp hạng lại không thể thêm đoạn chưa được lấy về"
        )

        invalidate("hybrid")
        lexical_tra_ve = bm25_search(FakeCollection(DOCS), "hybrid", "E-204", k=3)
        hop_nhat = reciprocal_rank_fusion([vector_tra_ve, lexical_tra_ve], k=5)

        assert doan_dung in hop_nhat, "sau khi hợp nhất, đoạn chứa mã lỗi phải có mặt"
        # RRF không trọng số: đoạn hạng 1 của nhánh vector và hạng 1 của nhánh từ khoá
        # có điểm BẰNG NHAU (cùng 1/(60+1)), nên đoạn mã lỗi hoà chứ không vượt lên.
        # Vẫn đủ: nó chắc chắn lọt TOP_K=5 đưa cho mô hình, trong khi trước đây nó
        # không hề có mặt. Muốn nó luôn đứng nhất thì phải đặt trọng số cho nhánh từ
        # khoá — chưa làm vì chưa có số liệu để chọn trọng số.
        assert doan_dung in hop_nhat[:2], "phải nằm trong nhóm đầu sau hợp nhất"


@pytest.mark.parametrize(
    "rows, query",
    [
        ([("Mã lỗi E-204: kiểm tra quang.", "single.pdf", 0)], "E-204"),
        ([("Mã lỗi E-204: kiểm tra quang.", "single.pdf", 0)], "204"),
        ([("nguồn", "a.pdf", 0), ("nguồn điện", "b.pdf", 1)], "nguồn"),
        ([("kiểm tra nguồn", "a.pdf", 0), ("kiểm tra quạt", "b.pdf", 0)], "nguồn"),
    ],
)
def test_small_corpus_and_common_terms_remain_retrievable(rows, query):
    name = "small-" + query + str(len(rows))
    invalidate(name)
    assert bm25_search(FakeCollection(rows), name, query, k=3)


@pytest.mark.parametrize(
    "query",
    [
        "Mã lỗi E-999 cần kiểm tra nguồn thế nào?",
        "GPON-9999Z hỗ trợ nguồn nào?",
    ],
)
def test_unknown_full_code_cannot_match_shared_words_or_model_family(query):
    invalidate("unknown-natural")
    assert bm25_search(FakeCollection(DOCS), "unknown-natural", query, k=5) == []


def test_zero_limit_does_not_return_a_spurious_hit():
    assert bm25_search(FakeCollection(DOCS), "zero-limit", "E-204", k=0) == []


def test_shared_compound_suffix_does_not_match_another_code():
    rows = [("Model GPON-2412B", "a.pdf", 0)]
    invalidate("same-suffix")
    assert bm25_search(FakeCollection(rows), "same-suffix", "EPON-2412B", 3) == []


def test_strict_mode_surfaces_index_failure_instead_of_false_empty_evidence():
    class BrokenCollection:
        def get(self, include=None):
            raise OSError("unavailable index")

    invalidate("broken-strict")
    with pytest.raises(OSError, match="unavailable index"):
        bm25_search(BrokenCollection(), "broken-strict", "E-204", 3, raise_errors=True)
