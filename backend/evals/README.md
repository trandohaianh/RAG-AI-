# Bộ eval FieldOps

Golden set: `datasets/fieldops-sop-golden-v1.json`, gồm 68 câu hỏi trên 8 SOP giả lập trong
`examples/fieldops-sop-demo`:

- 48 câu có đáp án
- 12 câu tài liệu không có thông tin (hệ thống phải từ chối)
- 8 câu an toàn / tấn công prompt / đa nguồn / diễn đạt lại

Dữ liệu chia `dev` (56 câu) và `holdout` (12 câu). Nhãn do người phát triển soạn, chưa được
chuyên gia hiện trường duyệt.

## Bộ eval chạy gì

Harness gọi đúng code của ứng dụng theo thứ tự:
`ingest` → Chroma + BM25 → RRF → rerank → prompt → `answer_rag_once` → kiểm tra trích dẫn.
Không có đường đi riêng nào chỉ dành cho bài test.

| Nhóm | Chỉ số |
|---|---|
| Truy xuất (mức tài liệu nguồn) | recall@k, precision@k, MRR, nDCG@k, hit rate |
| Câu trả lời (chấm xác định, không dùng LLM) | độ phủ dữ kiện (`facts`, mỗi nhóm có các cách diễn đạt tương đương, so khớp sau khi bỏ dấu), nội dung cấm (`forbidden`), từ chối đúng / từ chối nhầm, có trích dẫn `【E#】`, trích dẫn sai nhãn, độ bám nguồn theo từ vựng |
| LLM-judge (tuỳ chọn, `--judge`) | faithfulness, relevancy |
| Vận hành | độ trễ p50/p95, token vào/ra, chi phí USD mỗi câu (lấy từ chính bộ ghi chi phí của app) |

Một câu được tính **đạt** khi:
- Câu có đáp án: không từ chối, độ phủ dữ kiện ≥ 0.5, không trích dẫn sai, không có nội dung cấm.
- Câu không có đáp án: phải từ chối và không có nội dung cấm.

## Chạy

```bash
python backend/scripts/run_evals.py --mode offline                    # CI, không gọi API, ~3 giây
python backend/scripts/run_evals.py --mode live --split holdout       # Gemini thật (cần GEMINI_API_KEY)
python backend/scripts/run_evals.py --mode live --judge               # thêm LLM-judge
python backend/scripts/run_evals.py --mode offline --update-baseline  # chốt mốc mới sau khi cải tiến có chủ đích
```

Kết quả ghi vào `reports/evals/`: file `<thời điểm>-<mode>-<split>.json` và `.md`, `latest.md`,
và một dòng mới trong `history.jsonl` để theo dõi xu hướng.

Mã thoát khác 0 khi chạm ngưỡng trong `thresholds.json`, hoặc khi chỉ số tụt quá 0.03 so với
`baselines/<mode>-<split>.json`.

**Offline** dùng embedding băm và bộ sinh trích xuất, nên chỉ đo đường ống truy xuất và bộ chấm,
không đo chất lượng Gemini. Ngưỡng `live` hiện là ước lượng ban đầu; nên chỉnh lại sau 3–5 lần chạy
thật đầu tiên.

## Lưu ý khi đọc kết quả

- Kho có 8 tài liệu và k=5 nên recall@5 gần như luôn bằng 1. Hãy nhìn MRR và nDCG để biết tài liệu
  đúng có đứng đầu hay không.
- Độ bám nguồn là proxy từ vựng: bắt được thông số hay lệnh bịa thêm, nhưng không bắt được suy luận
  sai từ những từ đúng. Muốn kiểm tra kỹ hơn thì bật `--judge`.
- Muốn thêm câu hỏi: thêm vào JSON, chạy test `tests/test_evals.py::test_golden_set_is_well_formed`,
  rồi `--update-baseline`.

## Dev / holdout (từ 2026-11-17)

- **dev**: dùng để chỉnh tham số (chunk, top-k, rerank, ngưỡng). CI chỉ báo cáo, không chặn.
- **holdout**: đóng băng. CI chỉ chặn merge trên split này (`--split holdout`). Baseline
  `baselines/offline-holdout.json` lưu `holdout_fingerprint`; sửa bất kỳ câu holdout nào làm CI
  báo "holdout đã bị sửa". Muốn đổi holdout: tạo phiên bản dataset mới, rồi chốt baseline có chủ đích.
- Dung sai hồi quy tự nâng lên tối thiểu 1/n (một câu), vì 12 câu holdout thì một câu = 8 điểm.
- **Duyệt chuyên gia:** mỗi câu holdout cần `"reviewed_by"` (tên/đơn vị kỹ thuật viên duyệt) và
  `"reviewed_at"` (YYYY-MM-DD). `run_evals.py` in ra số câu đã duyệt / tổng, mục tiêu ≥100 câu do
  người nghiệp vụ viết và duyệt (hiện 0/12: nhãn do dev tự viết, chỉ đo được pipeline).
