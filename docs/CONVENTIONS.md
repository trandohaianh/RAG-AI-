# Quy ước dự án FieldOps

Tài liệu này là quy ước chung cho mọi người đóng góp. Công cụ (ruff, ESLint, Prettier, pre-commit, CI) kiểm phần máy kiểm được; phần còn lại dựa vào review.

## 1. Cấu trúc thư mục

```
backend/                 FastAPI — mỗi tính năng một package
  auth/  chat/  docs/  field/  rag/  costs/  billing/  embed/  public_api/  admin/  analytics/  saas/
  pipeline/              job vận hành (KPI, retention, backup) + Medallion bronze→silver→gold
  evals/  evaluation/    bộ đánh giá RAG (golden set, baseline, ngưỡng)
  validation/            kiểm chất lượng dữ liệu kiểu Great Expectations
  scripts/               script chạy tay / CI (không import từ ứng dụng ngược lại)
  database.py            engine + session
  schema_migrations.py   migration có đánh số phiên bản (CSDL)
  config.py              mọi biến môi trường đọc ở MỘT chỗ
  main.py                tạo app, gắn router, middleware
frontend/                Next.js 16 (App Router)
  app/                   route: (app)/ trang sau đăng nhập, (auth)/ đăng nhập, embed/ widget
  components/            component dùng lại; components/field/ cho màn ca hiện trường; components/ui/ nguyên tử UI
  lib/                   gọi API, phiên, hàng đợi offline, cache — không chứa JSX trừ provider
mobile/                  Expo (React Native): screens/, components/, lib/, navigation/
tests/                   pytest (backend) + client_state.test.cjs (logic frontend/mobile)
deploy/cloud/            1 VM + Caddy (HTTPS riêng của FieldOps); edge dùng chung là tuỳ chọn
deploy/production/       nhiều dịch vụ sau nginx, secrets dạng file
docs/  reports/          tài liệu kỹ thuật, báo cáo audit
```

Mỗi package tính năng ở backend theo cùng mẫu tên file:

| File | Vai trò |
|---|---|
| `router.py` | endpoint FastAPI: nhận request, kiểm quyền, gọi service, trả schema |
| `schemas.py` | Pydantic model vào/ra của API và hằng số trạng thái |
| `models.py` | bảng SQLAlchemy |
| `service.py` | nghiệp vụ không phụ thuộc HTTP (gọi được từ job, script, test) |

Cơ sở dữ liệu: mỗi package khai báo bảng trong `models.py`. Mọi thay đổi schema cho CSDL đã tồn tại phải đi qua một `Migration(n, "tên", hàm)` mới trong `backend/schema_migrations.py`. Không sửa migration đã phát hành. Thêm một phiên bản thì đồng thời cập nhật danh sách phiên bản mong đợi trong `tests/test_schema_migrations.py` và `tests/test_postgres_migrations.py`.

## 2. Quy tắc đặt tên

| Loại | Quy tắc | Ví dụ |
|---|---|---|
| Module/package Python | `snake_case` | `field/integrations.py` |
| Hàm, biến Python | `snake_case`; nội bộ module bắt đầu bằng `_` | `enforce_budget_async`, `_case_for_user` |
| Lớp Python | `PascalCase`; schema API kết thúc bằng `In`/`Out`/`Create`/`Update` | `CaseOut`, `StepUpdate` |
| Hằng số | `UPPER_SNAKE_CASE` | `MAX_UPLOAD_BYTES`, `CASE_STATUSES` |
| Biến môi trường | `UPPER_SNAKE_CASE`, đọc trong `config.py`; biến lộ ra trình duyệt bắt đầu bằng `NEXT_PUBLIC_` / `EXPO_PUBLIC_` | `LLM_DAILY_BUDGET_USD` |
| Bảng CSDL | `snake_case` số nhiều, tiền tố theo tính năng | `field_service_cases`, `llm_usage_events` |
| Index | `ix_<bảng>_<cột>` hoặc `ix_<tên ngắn>` | `ix_messages_created_at` |
| Endpoint | danh từ số nhiều, chữ thường, `-` giữa từ | `/field/cases/{case_id}/steps` |
| Component React | `PascalCase.tsx`, một component chính mỗi file | `CaseDispatchEditor.tsx` |
| Module TS không JSX | `camelCase.ts` | `offlineQueue.ts` |
| Hook React | `useXxx` | `useAuth` |
| Route Next | thư mục chữ thường; nhóm route trong `( )` | `app/(app)/field/schedule/page.tsx` |
| Test | `tests/test_<tính năng>.py`; hồi quy audit: `test_audit_regressions_roundN.py`, tên test mô tả hành vi | `test_logout_revokes_every_token_of_the_user` |
| Nhánh git | `<loại>/<mô tả-ngắn>` | `fullstack/fieldops-production`, `fix/webhook-deadline` |
| Commit | dòng đầu tiếng Anh, mệnh lệnh, ≤ 72 ký tự; thân giải thích lý do | `Run the scheduler in the production stack` |

Ngôn ngữ: giao diện và thông báo cho người dùng viết bằng tiếng Việt. Comment giải thích *vì sao* có thể viết tiếng Việt. Tên trong mã luôn bằng tiếng Anh.

## 3. Kích thước file

- Mục tiêu: một file mã nguồn **dưới ~800 dòng**.
- Khi vượt, tách theo tính năng: schema sang `schemas.py`, nghiệp vụ sang `service.py`, khối UI độc lập sang `components/<tính năng>/`.
- Chỉ tách nơi có test bao phủ. Nơi chưa có test thì thêm test trước, rồi mới tách.
- Hiện còn một ngoại lệ: `frontend/app/(app)/field/page.tsx` (xem `reports/08_Bo_khung_du_an.md`).

## 4. Lint và định dạng

| Phần | Công cụ | Lệnh |
|---|---|---|
| Python (`backend/`, `tests/`) | ruff (E, F, W, I, B, UP) + ruff format, cấu hình ở `pyproject.toml` | `ruff check backend tests` · `ruff format backend tests` |
| Frontend | ESLint (eslint-config-next) + Prettier | `cd frontend && npm run lint && npm run format:check` |
| Kiểu TypeScript | tsc | `cd frontend && npm run typecheck` |
| Mọi thứ trước khi commit | pre-commit | `pip install pre-commit && pre-commit install` (một lần), sau đó tự chạy mỗi lần commit |

- Job `lint` trong `.github/workflows/ci.yml` chạy đúng các bước trên và chặn merge khi lỗi.
- ESLint đang giới hạn tối đa 20 cảnh báo. Đó là các quy tắc kiểu React Compiler (`set-state-in-effect`) được hạ xuống mức cảnh báo có lý do.
- Không thêm cảnh báo mới: con số này chỉ được giảm.

## 5. Bí mật và file cá nhân

- Không bao giờ commit `.env`, `.env.local`, `TAI-KHOAN.md`, `evaluation.local`, `*.docx` hay thư mục `secrets/`.
  - `.gitignore` chặn các file này.
  - Hook pre-commit `forbid-secret-files` và bước kiểm trong CI cũng chặn.
- `*.env.example` chỉ chứa tên biến, giá trị rỗng hoặc ví dụ (`your_key_here`). Không bao giờ chứa khoá thật.
- Lộ khoá thì phải **thu hồi và cấp lại khoá** (rotate). Xoá khỏi lịch sử git là chưa đủ.

## 6. Commit theo mốc

Commit tại các mốc ổn định:
- **Trước** một thay đổi rủi ro (format toàn bộ, tách module lớn, migration, nâng phụ thuộc), để có điểm quay lại.
- **Sau** khi kiểm thử đạt: `pytest tests`, `npm run build`, client tests, và lint sạch.

Không gộp thay đổi cơ học (format) với thay đổi hành vi trong cùng một commit.
