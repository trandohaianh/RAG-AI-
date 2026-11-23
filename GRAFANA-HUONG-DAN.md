# Hướng dẫn dùng Grafana (project RAG Chat)

Grafana dùng để **xem biểu đồ sức khỏe API** (request/giây, độ trễ chat, lỗi ingest…).  
**Không** dùng để upload tài liệu hay chat — việc đó làm trên **RAG Chat** (http://localhost:3001).

---

## Bước 1 — Bật Grafana

### Cách nhanh (Windows)

```powershell
cd d:\self
.\start-grafana.ps1
```

### Hoặc Docker Compose

```powershell
cd d:\self
docker compose up -d --no-deps grafana prometheus
```

Đợi khoảng 30 giây.

| Dịch vụ | URL |
|---------|-----|
| **Grafana** | http://localhost:3002 |
| **Prometheus** (trong Docker, không mở cổng host) | `docker compose exec prometheus wget -qO- localhost:9090/metrics` |

---

## Bước 2 — Đăng nhập Grafana

| Ô | Giá trị |
|---|--------|
| Username | `admin` |
| Password | `gig` |

(Lấy từ `docker-compose.yml` — **không** dùng email đăng ký RAG.)

Lần đầu Grafana có thể bắt đổi mật khẩu — có thể **Skip** hoặc đặt mật khẩu mới.

---

## Bước 3 — Backend phải đang chạy

Grafana đọc số liệu từ **Prometheus** → Prometheus scrape endpoint:

`http://127.0.0.1:8000/metrics`

**Chạy API trước** (một trong hai):

```powershell
cd d:\self
.\start-web.ps1
```

hoặc chỉ backend:

```powershell
cd d:\self\backend
.\start.ps1
```

Kiểm tra: mở http://127.0.0.1:8000/metrics — phải thấy dòng kiểu `rag_http_requests_total`.

---

## Bước 4 — Mở dashboard có sẵn

1. Mở thẳng: http://localhost:3002/d/rag-de-overview  
   hoặc menu **☰** → **Dashboards** → thư mục **RAG Chatbot** → **RAG Chatbot — DE Overview**
2. Góc phải chọn thời gian **Last 15 minutes** → nút **Refresh**

### Nếu Dashboards trống (chỉ thấy Tags, không có dòng nào)

Dashboard chưa được load — làm một trong hai:

**A) Restart Grafana (khuyên dùng)**

```powershell
cd d:\self
docker compose restart grafana
```

Đợi 20 giây, refresh trang Dashboards.

**B) Import tay**

1. **Dashboards** → **New** → **Import**
2. **Upload** file `d:\self\monitoring\grafana\dashboards\rag-overview.json`
3. Chọn data source **Prometheus** → **Import**

Dashboard hiển thị:

| Panel | Ý nghĩa |
|-------|---------|
| HTTP requests (rate) | Số request API/giây |
| Ingest errors | Lỗi upload/index tài liệu (1h) |
| Chat latency (p95) | 95% câu chat trả lời trong bao lâu (giây) |
| Chunks retrieved | Mỗi câu hỏi lấy bao nhiêu chunk từ vector DB |
| Embed quota exceeded | Số lần vượt quota Gemini embedding |

**Nếu toàn số 0:** dùng app RAG (chat, upload file) vài phút rồi refresh dashboard (góc phải ↻).

---

## Bước 5 — Tạo dữ liệu để thấy biểu đồ

1. Mở RAG Chat → đăng nhập  
2. Upload 1 file PDF/TXT  
3. Hỏi vài câu trong Chat  
4. Quay lại Grafana → chọn khoảng thời gian **Last 15 minutes** → Refresh

---

## Kiểm tra Prometheus có nhận metrics không

1. Mở http://localhost:9090  
2. Tab **Status** → **Targets**  
3. Job `rag-backend` phải **UP** (màu xanh). Production cần tệp `monitoring/secrets/metrics_token` chứa đúng `METRICS_TOKEN`, không thì target báo 401

Nếu **DOWN**:

- Backend chưa chạy cổng 8000  
- Firewall chặn  
- Chỉ chạy Grafana mà không chạy API

Query thử trong Prometheus:

```promql
sum(rate(rag_http_requests_total[5m]))
```

---

## Grafana vs tab「Thống kê」trong RAG

| | Grafana | Thống kê (trong app) |
|---|---------|----------------------|
| Ai xem | DevOps / kỹ thuật | User / admin app |
| Dữ liệu | Metrics server (Prometheus) | SQLite (số hội thoại, docs/user) |
| Cần Docker | Có | Không |
| Login | `admin` / `gig` | Email RAG |

---

## Dừng Grafana

```powershell
cd d:\self
docker compose stop grafana prometheus
```

---

## Tài khoản (tóm tắt)

Xem thêm [TAI-KHOAN.md](TAI-KHOAN.md).
