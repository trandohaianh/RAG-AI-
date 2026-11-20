# FieldOps — Bộ khung dự án, tách HTTPS và chuẩn hoá mã nguồn

- **Ngày:** 2026-11-20
- **Nhánh:** `fullstack/fieldops-production`
- **Remote:** `github.com/trandohaianh/RAG-AI-`
- **Mốc so sánh:** `867acc5`

Yêu cầu của chủ dự án:
- Sửa các lỗi còn sửa được.
- Tách HTTPS của từng dự án: **"Mỗi dự án tự có HTTPS"**.
- Dựng bộ khung theo checklist: cấu trúc thư mục, README, .gitignore, .env.example, quy tắc đặt tên, lint/format, không có file khổng lồ, commit theo mốc.
- Đẩy lên GitHub.

Ràng buộc: không mở, không in `.env`, `TAI-KHOAN.md`, `evaluation.local`, `*.docx`.

## Các commit (theo mốc)

| Commit | Mốc |
|---|---|
| `8b19a39` | HTTPS riêng cho FieldOps; image API chạy non-root; deploy tự rollback |
| `75f2011` | Lint + format Python bằng ruff, sửa phần còn lại. **Mốc trước khi format toàn bộ backend là `8b19a39`.** |
| `e76582d` | ESLint + cấu hình Prettier cho frontend, sửa lỗi lint |
| `0b0d6bf` | Prettier format toàn bộ frontend (cơ học). **Mốc trước đó là `e76582d`.** |
| `0b9c562` | Bộ khung: CONVENTIONS, pre-commit, job lint trong CI, tách schema; bỏ theo dõi file cá nhân |
| *(commit báo cáo này)* | Báo cáo 08 + kết quả dry run |

Mỗi mốc chỉ được commit sau khi test đạt. Thay đổi cơ học (format) luôn tách khỏi thay đổi hành vi.

---

## 1. Cấu trúc thư mục rõ ràng, tách frontend / backend / database / module theo chức năng

**Hiện trạng:** đã tách sẵn từ trước:
- `backend/` (FastAPI) chia package theo tính năng: `auth`, `chat`, `docs`, `field`, `rag`, `costs`, `billing`, `embed`, `public_api`, `admin`, `analytics`, `saas`, `pipeline`, `evals`.
- `frontend/` (Next.js App Router: `app/`, `components/`, `lib/`).
- `mobile/` (Expo).
- `tests/`.
- `deploy/`.
- Database: `backend/database.py` (engine/session), `backend/schema_migrations.py` (migration có phiên bản), `models.py` trong từng package.

**Thay đổi:**
- Chuẩn hoá mẫu file trong mỗi package (`router.py` / `schemas.py` / `models.py` / `service.py`), ghi ở `docs/CONVENTIONS.md` §1.
- Áp mẫu này cho `field`: tách `backend/field/schemas.py` ra khỏi `field/router.py`.
- README mục "Project structure" được viết lại theo cấu trúc thật.

## 2. README

**Hiện trạng:** `README.md` đã có kiến trúc, tính năng, deploy và chạy local.

**Thay đổi:** viết lại mục "Project structure", và thêm vào "Tests & CI" các lệnh test, lint, format, pre-commit. Thêm liên kết tới `docs/CONVENTIONS.md`. Hướng dẫn deploy theo từng dự án nằm ở `deploy/cloud/README.md` (mục "HTTPS riêng cho mỗi dự án…").

## 3. .gitignore

**Hiện trạng:** đã chặn `.env`, `node_modules`, `.next`, dữ liệu runtime, `/*.docx`, `/evaluation.local/`, secrets của `deploy/production`.

**Thay đổi:**
- Chặn thêm ở mọi cấp: `TAI-KHOAN.md`, `*.docx`, `evaluation.local`, `.env.*` (trừ `.env.example`).
- Thêm `.gitattributes`: `Caddyfile.*` và `*.caddy` luôn dùng LF.

**Phát hiện quan trọng:**
- `TAI-KHOAN.md` và `Hệ thống.docx` **đã bị git theo dõi từ commit đầu tiên** và đã có trên GitHub (`origin/main` và nhánh này).
- Chủ dự án chọn: **bỏ theo dõi, giữ file trên máy** (`git rm --cached`; nội dung không bị mở hay sửa).
- Từ commit `0b9c562`, hai file không còn trong repo, nhưng **vẫn nằm trong lịch sử cũ trên GitHub**, vì không force-push.
  → **Nếu `TAI-KHOAN.md` chứa mật khẩu hay khoá thật, hãy đổi chúng ngay.**
  → Muốn xoá hẳn khỏi lịch sử thì chủ dự án tự chạy `git filter-repo` rồi force-push.

## 4. .env.example không chứa khoá bí mật

**Hiện trạng và kiểm tra:**
- Có ba file: `.env.example`, `deploy/cloud/.env.example`, `deploy/production/.env.example`.
- Mọi biến KEY/SECRET/TOKEN/PASSWORD đều rỗng hoặc là placeholder (`your_…`).
- Quét toàn bộ file đang được theo dõi với các mẫu khoá thật (Google `AIza…`, Stripe `sk_live/sk_test/whsec_`, GitHub `ghp_/github_pat_`, Slack `xox…`, AWS `AKIA…`, Resend `re_…`, private key PEM): **không có kết quả**.

**Thay đổi:** `deploy/cloud/.env.example` đặt trần chi phí AI mặc định là $2 cho cả hệ thống và $0.2 mỗi người dùng, theo quyết định ở vòng production-readiness.

## 5. Quy tắc đặt tên

**Thay đổi:** tạo mới `docs/CONVENTIONS.md`, gồm:
- §2: bảng quy tắc cho module, hàm, lớp, hằng số, biến môi trường, bảng và index CSDL, endpoint, component React, module TS, hook, route Next, test, nhánh git, commit message.
- §3: giới hạn kích thước file.
- §5: chính sách bí mật.
- §6: quy tắc commit theo mốc.

## 6. Lint và format

| Phần | Công cụ / cấu hình | Kết quả |
|---|---|---|
| Python `backend/` + `tests/` | ruff 0.14.0 — `pyproject.toml`: E, F, W, I, B, UP; dòng 120; cho phép `Depends()` mặc định (B008) và mẫu fixture pytest | Ban đầu **675** cảnh báo. Sau cấu hình hợp lý và `--fix` còn 5, đã sửa tay: `raise … from None` ở `field/integrations.py`, biến vòng lặp không dùng, ghi chú ContextVar bất biến. **Kết quả: `All checks passed!`** |
| Python format | `ruff format` | 137 file được format lại (cơ học); hiện **152 file đã đúng định dạng** |
| Frontend lint | ESLint 9.39.5 + `eslint-config-next` 16.4.5 (core-web-vitals + typescript) + `eslint-config-prettier`: `frontend/eslint.config.mjs` | Ban đầu **43** vấn đề. Đã sửa: `any` trong `MarkdownContent` (dùng kiểu `Components`, không còn rải `node` vào thẻ DOM), thứ tự callback và deps trong `ChatWindow`, deps memo ở trang ca, plugin Tailwind dạng ESM, dấu ngoặc kép chưa escape, ghi chú điều hướng cứng khi phiên hết hạn. **Kết quả: 0 lỗi, 20 cảnh báo** |
| Frontend format | Prettier 3.9.10: `.prettierrc.json` (width 120), `.prettierignore` | 49 file được format lại; `prettier --check .` → **All matched files use Prettier code style!** |
| TypeScript | `tsc --noEmit` | **0 lỗi** |
| Tự động trước commit | `.pre-commit-config.yaml` (ruff check/format, check-yaml, merge conflict, file lớn, private key, chặn file bí mật/cá nhân, eslint, prettier) + `.editorconfig` | `pre-commit run --all-files` chạy thật: mọi hook đạt. Riêng hook chặn file cá nhân **đã bắt được** `TAI-KHOAN.md` và `Hệ thống.docx` → xem §3 |
| CI | job `lint` mới trong `.github/workflows/ci.yml` | ruff check + format check, eslint `--max-warnings=20`, prettier check, chặn file bí mật/cá nhân. Bước này dùng `core.quotepath=off`, vì nếu không thì tên file có dấu bị git in trong ngoặc kép và lọt qua kiểm tra |

**20 cảnh báo ESLint còn lại:**
- Thuộc quy tắc mới kiểu React Compiler: `react-hooks/set-state-in-effect` và `react-hooks/immutability`.
- Chúng bắt mẫu "tải dữ liệu rồi `setState` trong `useEffect`", có ở khoảng 20 trang và component.
- Sửa đồng loạt mà không có test giao diện là rủi ro đổi hành vi. Vì vậy chúng được hạ xuống mức cảnh báo (có ghi lý do trong `eslint.config.mjs`) và CI khoá **tối đa 20**: con số này chỉ được giảm.
- Hướng xử lý: chuyển dần sang dữ liệu từ server hoặc `use()`.

**Cách chạy:**
```bash
ruff check backend tests && ruff format --check backend tests
cd frontend && npm run lint && npm run format:check && npm run typecheck
pip install pre-commit && pre-commit install      # một lần; sau đó tự chạy khi commit
```

## 7. Không viết toàn bộ phần mềm trong một file lớn

Quét các file `.py/.ts/.tsx/.cjs` đang được theo dõi, sau khi format:

| File | Dòng | Xử lý |
|---|---|---|
| `frontend/app/(app)/field/page.tsx` | 1067 | **Chưa tách.** Đây là file duy nhất vượt 800 dòng, nhưng không có test bao phủ (client test chỉ phủ `lib/`), nên theo yêu cầu chưa tách. Kế hoạch: viết test E2E (Playwright) cho luồng ca trước, rồi tách thành `components/field/CaseQueue`, `KnowledgePanel` và `OfflineBanner`. |
| `backend/field/router.py` | 750 → **638** | Tách hằng số trạng thái và 13 schema Pydantic sang `backend/field/schemas.py` (142 dòng). Được bao phủ bởi `test_field_operations.py`, `test_field_dispatch_photos_integrations.py` và các bộ hồi quy. |
| `backend/field/integrations.py` | 749 | Dưới ngưỡng. Test monkeypatch trực tiếp các tên trong module này, nên chưa tách. |
| `mobile/lib/api.ts`, `mobile/lib/fieldOffline.ts` | 681, 653 | Dưới ngưỡng. |

## 8. Commit tại các mốc ổn định

- Xem bảng commit ở đầu báo cáo.
- Trước mỗi thay đổi rủi ro đều có một commit đã kiểm thử: `8b19a39` trước khi format backend, `e76582d` trước Prettier.
- Mỗi commit chỉ được tạo sau khi test đạt.

---

## 9. Sửa các mục còn lại của báo cáo 06 §4

| Mục | Thay đổi | File |
|---|---|---|
| Image API chạy root trên cloud/Render | Thêm user `app` (uid 1000) và `USER app`. `deploy.sh` tự `chown` volume cũ (từ thời còn chạy root) đúng một lần qua `compose run --user root … chown`. Ghi chú cho Railway: volume luôn thuộc root, cần đặt `RAILWAY_RUN_UID=0` nếu gắn volume. | `backend/Dockerfile`, `deploy/cloud/deploy.sh` |
| Không tự rollback | `deploy.sh` gắn tag `:previous` cho image đang chạy trước khi build. Nếu `smoke.sh` thất bại, nó gắn lại tag cũ, `up -d --no-build`, rồi báo lỗi. Có hướng dẫn khôi phục `app.db` khi bản mới đã chạy migration. | `deploy/cloud/deploy.sh`, `deploy/cloud/README.md` |

**Còn lại (cần giá trị hoặc hạ tầng của chủ dự án):** khoá Gemini, tên miền, URL công khai trên Render/Railway, `TRUSTED_PROXY_HOPS` đo trên deploy thật, và `BACKUP_DIR` đặt ra ngoài máy.

## 10. Tách HTTPS: mỗi dự án tự có HTTPS

**Trước đây:**
- Caddy của FieldOps là "cổng chung": nối mạng `edge` và import `/opt/edge/sites/*.caddy`.
- EduDSS và Telecom phụ thuộc vào Caddy này.

**Bây giờ (mặc định):**
- `deploy/cloud/docker-compose.yml`: Caddy **chỉ phục vụ FieldOps**. Không còn mạng `edge`, không còn mount `/opt/edge/sites`.
- `Caddyfile` không còn `import /etc/caddy/sites/*.caddy`.
- `deploy.sh` và `setup-vm.sh` không còn tạo mạng `edge`.
- FieldOps deploy độc lập.

**Tuỳ chọn tiết kiệm VM ("edge dùng chung"):**
- `SHARED_EDGE=1 bash deploy.sh` thêm `docker-compose.shared-edge.yml`:
  - mạng `edge` (external);
  - mount `/opt/edge/sites`;
  - `Caddyfile.shared-edge`, import `Caddyfile` của FieldOps và `sites/*.caddy`.
- Cách bật/tắt và việc dự án kia phải làm có trong `deploy/cloud/README.md`.
- Mã của Telecom/EduDSS không bị sửa; các phiên làm dự án đó tự gỡ phụ thuộc.

**Test:**
- `test_fieldops_caddy_serves_only_fieldops_by_default`
- `test_shared_edge_is_an_opt_in_overlay`
- `test_api_image_runs_as_non_root_and_deploy_can_roll_back`

## 11. Kiểm thử cuối

| Bộ | Kết quả |
|---|---|
| Backend pytest (WSL, Python 3.12) | **613 passed, 12 skipped**. Một lần chạy lúc máy quá tải (Docker và các phiên khác chạy cùng lúc, 11 phút thay vì 7) có 2 test nhạy thời gian fail; chạy lại thì đạt toàn bộ. |
| Client tests (Node) | **23/23** |
| Frontend | tsc 0 lỗi; ESLint 0 lỗi / 20 cảnh báo; Prettier sạch; `next build` thành công (cả chế độ `/api`); `npm audit` 0 lỗ hổng |
| Ruff | check sạch; format sạch (152 file) |
| pre-commit | mọi hook đạt (sau khi bỏ theo dõi 2 file cá nhân) |
| Dry run Docker | xem §12 |

## 12. Dry run Docker (mã cuối)

**Cách chạy:**
- Docker Desktop 29.8.0.
- Bản sao `deploy/cloud` đặt trong thư mục tạm (thư mục thật không có `.env` nào được đọc), cùng env dùng một lần (`fieldops.localhost`, khoá ngẫu nhiên).
- Chế độ **mặc định**, tức HTTPS riêng: `up -d --build --wait` → kiểm tra → `down -v`.
- Không còn container, volume hay network nào sót lại. Docker không bị tắt.

**Kết quả:**

| Kiểm tra | Kết quả |
|---|---|
| Trạng thái service | api **healthy**, web **healthy**, caddy running, pipeline running |
| User chạy tiến trình | API uid **1000**, web uid **1000** (`node`), chủ sở hữu `/app/data` là 1000 |
| Caddy chỉ phục vụ FieldOps | Caddy chỉ nối mạng `fieldops-dryrun2_default`, Caddyfile không có `import`, không mount `/opt/edge/sites` |
| smoke qua Caddy HTTPS | web 200, health 200, auth/config 200, metrics 401 / 200 (có token). `ready` 503 `llm: missing_key`, đúng vì không đặt khoá Gemini; `storage: ok` khi chạy bằng uid 1000 |
| E2E qua Caddy | luồng chính **17/17**, luồng biên **16/16** |
| Bước chown của `deploy.sh` | Giả lập volume cũ: chown `/app/data` cùng một file về root. Lệnh chown trong `deploy.sh` đưa cả hai về **1000**; API ghi được bằng uid 1000 |
| Edge dùng chung (tuỳ chọn) | `caddy validate` → **Valid configuration**; route phục vụ cả `fieldops.localhost` và site thử `other.localhost`; `docker compose … -f docker-compose.shared-edge.yml config --quiet` hợp lệ |
