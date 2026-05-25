# Tài khoản — RAG Chat vs Grafana

**Hai app, hai bộ login — không dùng chung email/mật khẩu.**

---

## 1. RAG Chat (app hỏi tài liệu)

| | |
|---|---|
| **URL** | http://localhost:3001 (hoặc 3000 nếu không bị Docker chiếm) |
| **Đăng ký** | Trang **Đăng ký** — email + mật khẩu bạn tự chọn (≥ 6 ký tự) |
| **Admin RAG** | **User đăng ký đầu tiên** trong database → `role = admin` |
| **Admin làm gì** | Vào tab **Admin** trong app (xem users, thống kê) |

**Không có sẵn user `admin@...` trong RAG** — bạn phải **Đăng ký** lần đầu; người đăng ký đầu = admin.

### Xem ai là admin RAG hiện tại

```powershell
cd d:\self\backend
.\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('../data/app.db'); print([r for r in c.execute('SELECT id,email,role FROM users')])"
```

---

## 2. Grafana (chỉ giám sát server — ops)

| | |
|---|---|
| **URL** | http://localhost:3002 (sau `docker compose up`) |
| **User** | `admin` |
| **Password** | `gig` |
| **Cấu hình** | `docker-compose.yml` → `GF_SECURITY_ADMIN_USER` / `GF_SECURITY_ADMIN_PASSWORD` |

**Email đăng ký RAG không dùng được trên Grafana.**

### Grafana không đăng nhập được — thường vì:

1. **Chưa chạy Grafana** (chỉ chạy `npm run dev` + uvicorn thì **không có** Grafana):

   ```powershell
   cd d:\self
   docker compose up -d grafana prometheus
   ```

   Đợi ~30 giây, mở http://localhost:3002

2. **Mở nhầm cổng** — `localhost:3000` có thể là Grafana cũ từ Docker, không phải RAG; login `admin`/`gig` chỉ chắc chắn trên **3002** (theo compose hiện tại).

3. **Nhập nhầm** — dùng email RAG thay vì user `admin`.

---

Hướng dẫn chi tiết từng bước: **[GRAFANA-HUONG-DAN.md](GRAFANA-HUONG-DAN.md)**

## Tóm tắt nhanh

| | RAG Chat | Grafana |
|---|----------|---------|
| Cần để chat tài liệu? | **Có** | Không |
| Login | Email bạn đăng ký | `admin` / `gig` |
| Admin | User đăng ký **đầu tiên** | Luôn là `admin` (Grafana) |
| Chạy bằng | `.\start-web.ps1` | `docker compose up -d grafana` |
