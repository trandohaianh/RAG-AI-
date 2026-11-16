# Final Production Audit — 28/10/2026

Đã sửa các đường vận hành và tạo bộ đóng gói tại `deploy/production/`. Root Compose
hiện trỏ về cấu hình production; mặc định chỉ gateway publish 80/443. Audit không
triển khai project production, không cài CA vào OS/browser, không cấp ACME public,
không gọi paid AI API và không nhập dữ liệu cũ vào volumes mới.

## Phạm vi có bằng chứng

| Hệ | Source đã xác định |
|---|---|
| RAG | `D:/self/backend`, frontend hiện tại |
| Telecom | `D:/Du_An_1_Telecom_AI_Full/v2/ram_online`; review lifecycle server/voice và producer Spark |
| Edu | `D:/KLTN/edudss-final-candidate-v2/engine` — đúng package FastAPI/Celery của image hiện có |

Không sửa nhầm Edu Streamlit tại `D:/KLTN/code/app`. Bản source đóng gói Edu/Telecom
khớp source gốc theo SHA-256 trong `source-manifest.json`. Patch voice bổ sung nằm
trong `patches/telecom-voice-lifespan.patch` và đã áp dụng vào source gốc.

## Phát hiện và bản sửa

| Rủi ro đã xác định | Sửa/kiểm chứng |
|---|---|
| Compose mở trực tiếp API, Redis, PostgreSQL và các UI admin/demo; có mật khẩu mẫu | Root production Compose thay topology cũ; chỉ 80/443; secrets ngẫu nhiên bằng file, data/broker không nối mạng gateway |
| Redis cache RAG tạo client lỗi rồi bỏ tham chiếu; race tạo client; không shutdown | Pool hữu hạn, owner rõ, lock khởi tạo, close cả client/pool khi ping lỗi và ở lifespan finally; cooldown kết nối thất bại |
| Edu API thiếu cleanup và task training tạo client mỗi lần không đóng | Lifespan/partial-startup cleanup; task finally đóng cả lúc retry/lock thất bại/training lỗi; không đóng tài nguyên inject từ ngoài |
| Celery child giữ working set ML qua nhiều task | Prefork, concurrency/prefetch giới hạn, recycle theo 50 task và KiB, cgroup 2GiB, giới hạn BLAS/OpenMP; worker signals đóng cache/pool trước fork và khi recycle |
| Redis lỗi lan thành call timeout/exception | FailOpenGate bọc OnlineGate; OPEN không I/O, HALF_OPEN một probe, epoch chống late success; unknown cho phép cuộc gọi và ẩn trạng thái, không fail-open auth |
| Kafka constructor/subscribe lỗi nằm ngoài finally; cancellation có thể close trong khi poll còn chạy | AsyncExitStack, callbacks đăng ký ngay khi sở hữu resource, serialized Confluent poll/commit/close, SIGTERM cleanup; commit sau xử lý thành công |
| Log consumer in số thuê bao | Loại caller khỏi log result; gateway không log query string, auth headers, key hay body |
| SSE bị proxy/compression giữ buffer | Tắt buffering/cache/gzip riêng SSE; gzip vẫn hoạt động response thường; test nhận hai frame cách nhau ~1,2s |
| TLS/hostname/proxy headers không có trust boundary | Nginx 1.30.6, TLS1.2/1.3, reject Host/SNI lạ, ghi đè forwarded headers, Uvicorn chỉ tin IP proxy; certificate/key mount read-only |
| Gateway có thể nhìn thấy private CA/account keys khi mount cả thư mục | Chỉ mount leaf pair local; ACME account nằm riêng Certbot; export hook xác minh key/cert match rồi cấp pair riêng cho gateway |
| IP tự cấp có thể chiếm IP proxy tĩnh | `ip_range` tách khỏi IP .2 của gateway; healthcheck local phát hiện Nginx restart thay vì báo stack sẵn sàng giả |
| Drop capabilities làm Nginx không tạo temp path/chuyển owner/signal worker | Giữ đúng quyền root-master cần trong container; filesystem read-only + tmpfs/volumes giới hạn; forced rotation/HUP test qua |
| Config logrotate từ Windows có CRLF và mode executable | LF và 0644; volume logs có retention config và host-side rotation script; forced rotation/reopen qua |
| Router voice không close khi lifespan raise | Đặt service.close trong finally; chạy trực tiếp context manager với service giả, không nạp model/audio |

Producer Spark đã có finally-close trước audit; không viết lại thành một contract
Kafka khác. Aiokafka helper dùng stop() đúng thư viện; Confluent Consumer dùng close().
Không tuyên bố finally có thể chạy khi SIGKILL/OOM/mất điện.

## Kết quả kiểm tra

- Build thật năm image: gateway, Telecom, Edu, RAG backend và Next frontend.
- Full backend RAG: **415 passed, 10 skipped**, một warning JWT dự kiến từ ca sai thuật toán.
  Mười ca skip là PostgreSQL opt-in của lần chạy host, không được tính là đã pass.
- Hardening/startup suite cuối: **21 passed** (có overlap với full suite RAG; không cộng dồn).
- Edu: **95 passed** ở full suite; lifecycle suite cuối **8 passed**, gồm một ca thêm sau
  full run. Các cảnh báo LightGBM feature names/Starlette đã được ghi nhận, không che lỗi.
- Telecom contract gốc dùng Redis client 8.1.0: **9 passed, 8 deselected**; gồm Kafka
  commit đúng thứ tự, cancellation/admission, late deadline, Bloom và phone validation.
- Docker acceptance: **18/18 passed** bằng project UUID riêng, actual Linux containers.
  Bao gồm actual app/web health, trusted local TLS, HTTP308, Host/SNI rejection, metrics
  không public, known call policy, Redis stop/recovery với AOF, Kafka mTLS tới Lua sink,
  no-cert rejection, controller listener không reachable từ client, live Celery prefork
  với max-tasks=50, forwarded-header overwrite, gzip, SSE, DNS recreate và forced
  log rotation/HUP/worker signals.
- SSE fixture tổng hợp: frame đầu ngay, frame sau khoảng 1,2s. Đây là kiểm tra proxy,
  không phải benchmark LLM/token generation hoặc độ chính xác RAG.
- Compose config, image build và kiểm tra syntax đều qua. `verification.json` chứa
  report máy đọc được; `images.lock.json` lưu image IDs/digests đã thử.
- Tất cả project/container/volume test đã được dọn theo ownership. `data/app.db`
  giữ 208896 bytes và mốc sửa 11/10/2026 23:56:40 local; không migrate dữ liệu thật.

Số test ở đây là bằng chứng chạy trong đợt này, không tự xác nhận lại tuyên bố hơn
1.300 test của toàn bộ các nhánh/sản phẩm. Không cộng kết quả các lần chạy trùng.

## Giới hạn còn lại

1. Chưa có domain public. Local CA/leaf đã tạo, nhưng operator phải chủ động trust CA
   để Chrome/Edge hết cảnh báo. Let's Encrypt/renewal được chuẩn bị, chưa cấp cert thật.
2. Đây là single-host: Redis một node, Kafka RF=1. Chưa kiểm tra cluster HA, failover,
   network blackhole hoặc SLA SIP/HTTP ở tải đỉnh. Budget lookup không phải hard real-time.
3. Có cgroup limit và recycle config; chưa chứng nhận peak RAM của mọi workload ML hoặc
   thực hiện thử OOM trên task thật. Memory recycle diễn ra sau task, không chặn peak.
4. Chroma vẫn 1.5.9, đã xác minh PyPI còn phát hành version này. Advisory từ kiểm toán
   dependency trước chưa được coi là đã vá; chỉ dùng PersistentClient nội bộ, không
   expose Chroma HTTP/config tùy ý. Đợt này không phải kết quả dependency/OS scan sạch.
5. Chưa chạy provider/LLM/OCR thật. Cần cấu hình key/model và kiểm chứng bằng tài liệu
   đã review. Technical RAG parent-child của đợt trước vẫn opt-in, không tự reindex hoặc
   đổi endpoint. Không chứng nhận số liệu kỹ thuật đúng 100% hay thao tác phần cứng an toàn.
6. Backups/restore drills, reviewer governance, log tập trung, retention dữ liệu, capacity,
   migration dữ liệu cũ và pentest bên ngoài vẫn phải theo môi trường vận hành thực.

Hướng dẫn đầy đủ: [deploy/production/README.md](../deploy/production/README.md).
Nguồn primary đã đối chiếu: [Nginx security](https://nginx.org/en/security_advisories.html),
[Redis async ownership](https://redis.readthedocs.io/en/latest/examples/asyncio_examples.html),
[Celery workers](https://docs.celeryq.dev/en/stable/userguide/workers.html),
[Docker networks](https://docs.docker.com/reference/compose-file/networks/),
[Certbot](https://eff-certbot.readthedocs.io/en/stable/using.html).
