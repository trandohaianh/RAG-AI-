# Bộ triển khai ba hệ thống — audit 28/10/2026

Nguồn đã đối chiếu: RAG `D:/self`; Telecom `D:/Du_An_1_Telecom_AI_Full/v2/ram_online`;
Edu FastAPI/Celery `D:/KLTN/edudss-final-candidate-v2/engine`. Bản Streamlit tại
`D:/KLTN/code/app` là một nhánh khác, không bị sửa nhầm. Thư mục `edu/` và
`telecom/` chứa source snapshot đã sửa, đối chiếu hash bằng `source-manifest.json`.

Đây là bộ đóng gói **single-host**, chưa phải cluster HA. Các image đã build thật;
chi tiết kiểm chứng cuối cùng nằm trong `docs/FINAL-PRODUCTION-AUDIT.md`.

## Nginx và SSL termination

Client thiết lập TLS với Nginx. Nginx giữ certificate/key, xác minh hostname, xử lý
HTTP redirect, connection reuse, giới hạn body/timeout và chuyển request HTTP qua
mạng Docker riêng đến FastAPI. FastAPI không mở cổng trực tiếp ra host. TLS kết thúc
ở gateway; đoạn HTTP nội bộ cùng host không được gọi là end-to-end TLS.

Chỉ 80/443 được publish. Unknown Host/SNI bị từ chối. Proxy ghi đè X-Forwarded-*
và Uvicorn chỉ tin IP Nginx đã cấu hình. Docker DNS resolve giúp proxy theo IP mới
khi recreate backend; dải IP tự cấp tách khỏi IP proxy cố định. Không bật HTTP/3.

Gzip dành cho response thông thường/static; SSE `/chat/*` và `/embed/chat` tắt gzip,
buffering và cache. Upload request buffering vẫn bật để bảo vệ upstream khỏi slow
uploads. Không tăng buffer vô hạn để làm streaming nhanh. Nginx có health endpoint
chỉ bind loopback trong container, không publish port health ra host.

## Khởi tạo local lần đầu

Từ gốc repo, dùng Python 3.12 và Docker Compose >=2.24.4 (smoke test dùng `!override`).
Root `docker-compose.yml` include bộ production, thay cho topology mở cổng trực tiếp
trước đây. Bản cấu hình cũ chỉ lưu trong thư mục audit tạm để đối chiếu.

```powershell
py -3.12 -m venv .ops-runtime/.venv
.ops-runtime/.venv/Scripts/python.exe -m pip install -r deploy/production/ops-requirements.txt
.ops-runtime/.venv/Scripts/python.exe deploy/production/bootstrap_local.py
```

Bootstrap tạo secrets ngẫu nhiên, CA local, leaf certificate có SAN cho bốn hostname
và Kafka private CA/mTLS. Không ghi đè nếu đã có `.env`, `secrets` hoặc `tls`, không
in mật khẩu, không đăng ký DNS và không tự tin cậy CA trong OS. Trên workspace đã
chạy bootstrap trong audit, dùng bộ đã tạo thay vì chạy lại để tránh xoay khóa.

Bốn URL local:

- `https://rag.localhost`: web RAG.
- `https://api-rag.localhost`: REST/SSE RAG.
- `https://telecom.localhost/v1/check-call`: API policy cuộc gọi, cần X-API-Key.
- `https://edu.localhost`: REST Edu; `/health`, `/ready`, các route `/v1` có JWT/role/enrollment.

Nếu DNS resolver không hỗ trợ `*.localhost`, thêm bốn hostname vào hosts của máy
client trỏ `127.0.0.1` (hoặc IP máy Docker nếu truy cập từ thiết bị khác; cert vẫn phải
khớp hostname). Trình duyệt chỉ hết cảnh báo sau khi máy client tin CA local.

Windows CurrentUser trust, thao tác do operator chủ động thực hiện sau khi kiểm tra CA:

```powershell
certutil -dump deploy/production/tls/local-ca.crt
certutil -user -addstore Root deploy/production/tls/local-ca.crt
```

Không import private key CA vào trình duyệt. Gateway không mount khóa CA local,
khóa Kafka CA hoặc ACME account: chỉ nhận leaf certificate/key cần để phục vụ TLS.
Secrets file của Compose là bind files, không phải kho secrets mã hóa. Linux dùng
thư mục host 0700; Windows cần ACL thư mục riêng phù hợp. Không commit, email hay
đưa `secrets/`, `tls/` và `.env` vào artifact. Giữ private CA keys ngoài container.

## Build và chạy

```powershell
docker compose config --quiet
docker compose build
docker compose up -d --wait
docker compose ps
```

Lệnh `up` ở trên là thao tác triển khai do operator thực hiện. Trong audit chỉ chạy
project test riêng trên loopback, không bật project production mặc định 80/443.
`nginx.conf` là template chỉ thay các biến được whitelist trong entrypoint; các biến
Nginx như `$host`, `$uri`, `$remote_addr` được giữ nguyên.

Named volumes mới chứa dữ liệu production; **không tự nhập dữ liệu SQLite/uploads
cũ của repo**. Lập kế hoạch migrate/backup riêng trước cutover. RAG dùng PostgreSQL
role `rag_app` không phải superuser; Chroma/uploads nằm trên `rag_data`. Edu lưu job,
model artifacts/cache trên Redis AOF; policy, analytics, RAG cache và Celery/Edu
state dùng Redis instance/volume riêng. Kafka log nằm trên volume riêng.

IPAM mặc định `172.30.10/11/12.0/24`; đổi subnet phải đổi IP Nginx tương ứng và
`--forwarded-allow-ips`. Không dùng `*`. RAG có egress network riêng để gọi dịch vụ
ngoài; internal network không có egress. Gateway không nối mạng database/broker.

## Cấu hình model/provider trước khi dùng AI thật

Base stack không chứa cloud API key và không tự pull model. Cấu hình LLM/Ollama và
embedding theo backend bạn sử dụng; `OLLAMA_URL` cần trỏ endpoint thực sự reachable.
Backend RAG hiện tại vẫn cần Gemini cho embedding/OCR ngay cả khi dùng Ollama sinh
văn bản. Module technical parent-child của đợt trước vẫn opt-in; bộ DevOps không tự
review tài liệu hay đấu lại endpoint/reindex tài liệu khách hàng.

Để dùng Gemini, đặt API key vào file UTF-8 `deploy/production/secrets/gemini_api_key`,
không đặt literal key trong YAML hoặc argv. Sau đó:

```powershell
docker compose --env-file deploy/production/.env -f deploy/production/docker-compose.yml -f deploy/production/docker-compose.gemini.yml up -d --build
```

Đối với Edu, cấu hình provider URL/model/API key tương ứng bằng một override file
secrets theo cùng cơ chế `*_FILE`, giữ DEMO=0. Đăng nhập/quyền của engine dùng JWT
issuer tin cậy; không đưa signing key ra browser. Audit không gọi paid model API.

## Telecom: unknown không phải một kết quả policy đã biết

`OnlineGate` gốc giữ contract bool hoặc `OnlineUnavailable`. Adapter `FailOpenGate`
bọc lookup bằng circuit breaker và xuất kết quả có kiểu. Redis lỗi/deadline/circuit
mở trả `allowed=true`, `policy_status=unavailable`, `display_status=hidden`. Không
bịa một kết quả "policy đã cho phép". Auth sai vẫn 401, không fail-open.

OPEN trả từ RAM không ping Redis; HALF_OPEN chỉ một probe; permit có epoch để late
success không đóng nhầm circuit mới. Dùng đồng hồ monotonic; propagate cancellation.
Giới hạn in-flight, Redis socket timeout/retry=0 và budget 5ms nằm ở lookup core.
Đây không phải chứng nhận latency HTTP/SIP end-to-end hoặc hard real-time guarantee.
Caller telecom còn cần chính sách transport-timeout khi cả API/gateway không truy cập được.

Policy không tự được khởi tạo rỗng sau mất dữ liệu. Provision/restore bằng thao tác
admin có kiểm chứng; không thêm API public cho việc này. Consumer không bật
`--activate-demo-blocks`. Demo threshold không được dùng để tự chặn cuộc gọi thật.

`core.make_client` là single-shard Redis client; bộ Compose không dựng Redis Cluster.
Lua keys có hash tag và wrapper không phụ thuộc topology, nhưng chưa xác minh
Redis Cluster failover/cross-node routing trên môi trường này.

## Celery và lifecycle

CPU worker dùng prefork/concurrency=1, max-tasks-per-child=50, memory recycle=786432
KiB; LLM worker concurrency=2, memory recycle=524288 KiB. Mỗi container có cgroup
limit 2GiB, prefetch=1 và giới hạn BLAS/OpenMP threads. Đây là giá trị khởi điểm để
benchmark theo working set của bạn.

Memory recycle xảy ra sau task, không chặn peak của một task đang chạy. Cgroup mới
là hard container limit; OOM có thể kill process. CPU task hiện có soft/hard limits
850/900 giây; grace period tương ứng. Retry/acks_late dựa trên idempotency đã có.

Redis client/pool có owner rõ: client inject từ ngoài không tự bị đóng; resource tự
tạo được đóng ở lifespan/task-finally và prefork/recycle signals. Confluent
poll/commit/close chạy tuần tự một thread; SIGTERM đi qua cleanup; offset chỉ commit
sau xử lý thành công. Message lỗi dừng consumer, không tiếp tục commit vượt lỗi.
Producer Spark hiện có finally-close; pipeline Kafka không nằm trên call hot path.

Không thể chạy Python finally khi SIGKILL, OOM kill hoặc mất điện. Khi đó OS thu hồi
socket; correctness dựa trên persistent state, offset và idempotency, không dựa vào
promise "close luôn chạy kể cả crash".

## Kafka security và giới hạn single-host

Broker dùng mTLS, hostname verification và ACL deny-by-default. Principal dùng DN
thực `User:CN=telecom-analytics`, chỉ READ/DESCRIBE topic và READ group được cấp.
Client không có certificate bị từ chối. Controller PLAINTEXT chỉ bind 127.0.0.1
trong cùng container; ANONYMOUS là superuser để controller loopback hoạt động,
không được đổi controller listener thành 0.0.0.0 hoặc nới client-auth của broker.

Một node, replication factor=1 không có HA. Production cần HA phải thiết kế quorum,
replication/min ISR, backup/restore và failover riêng. Tách network không thay mTLS/ACL.
Poison message/DLQ và retention/capacity cần chốt theo yêu cầu vận hành, không tự skip lỗi.

## Khi có tên miền public: Let's Encrypt

Đổi bốn domain trong `.env`, trỏ DNS về máy public, mở firewall chỉ 80/443 cho stack.
Chạy Nginx bằng local certificate ban đầu để phục vụ ACME HTTP challenge; path
`/.well-known/acme-challenge/` trên port 80 là ngoại lệ redirect. Luồng này dùng DNS
hostname public, không xin chứng chỉ cho `.localhost`.

```sh
docker compose --env-file deploy/production/.env -f deploy/production/docker-compose.yml --profile certificates run --rm certbot certonly --webroot -w /var/www/acme --cert-name ai-systems --email YOUR_EMAIL --agree-tos --non-interactive -d RAG_DOMAIN -d RAG_API_DOMAIN -d TELECOM_DOMAIN -d EDU_DOMAIN --deploy-hook 'python /opt/export-certificates.py'
```

Thay các placeholder bằng domain/email thật. Sau khi cấp thành công, đổi:
`TLS_CERT_FILE=/etc/nginx/issued/fullchain.pem`, `TLS_KEY_FILE=/etc/nginx/issued/privkey.pem`.
Hook kiểm tra certificate/key match và chỉ export leaf pair sang volume gateway.
Bật HSTS_MAX_AGE=31536000 sau khi xác minh trusted HTTPS và hostname hoạt động đúng.
Build lại frontend để đổi URL API được nhúng lúc build, rồi recreate các app/gateway.

Chạy `sh deploy/production/renew-certificates.sh` hai lần mỗi ngày bằng timer/cron
của host. Script renew -> nginx -t -> HUP; không mount Docker socket vào Certbot.
Không chạy ACME thật trong audit vì chưa có domain và chưa có yêu cầu cấp cert public.

## Logs và kiểm chứng lại

Nginx access/error logs lưu trên `nginx_logs`; access log JSON không có query string,
Authorization, API key hoặc request body. Docker log driver có giới hạn dung lượng.
Đặt timer host chạy `sh deploy/production/rotate-logs.sh` mỗi giờ để thực thi rotation
20MiB/7 archives và reopen log bằng USR1. Log rotation là retention hữu hạn, không phải
kho audit bất biến; log tập trung/backup cần chính sách riêng.

```powershell
.ops-runtime/.venv/Scripts/python.exe deploy/production/smoke_production.py
```

Smoke test dùng project UUID riêng, volumes riêng, loopback 18080/18443, dữ liệu tổng
hợp và upstream SSE fixture được ghi rõ. Nó kiểm tra actual API health, TLS/redirect,
SNI/Host, Redis outage/recovery, Kafka mTLS tới Lua sink, prefork worker, streaming,
gzip và DNS recreate. Không gọi Gemini/Cohere/LLM. Finally dọn đúng project/volumes
test, không dùng `docker system prune` hoặc xóa dữ liệu production.
