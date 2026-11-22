# FieldOps — Tài liệu yêu cầu sản phẩm (PRD)

- **Trạng thái:** **ĐÃ CHỐT v1.0 (2026-11-22).** Chủ dự án đã xác nhận người dùng, quy mô, ngân sách, phạm vi MVP, cách xử lý dữ liệu AI, cách chia giai đoạn và hạn bàn giao. Các mục ⚠ còn lại là việc của bước 7–8, không chặn bước 2.
- **Ngày:** 2026-11-22.
- **Cơ sở soạn thảo:** phần mềm hiện có (mã nguồn, README) và các báo cáo `reports/01`–`08`.
- **Lưu ý:** phần mềm đã được xây trước khi có PRD. Tài liệu này ghi lại yêu cầu **đúng như sản phẩm đang làm**, rồi chốt phạm vi MVP và tiêu chí nghiệm thu để đo "đã đạt hay chưa".

---

## 0. Quyết định của chủ dự án (2026-11-22)

| Câu hỏi | Trả lời |
|---|---|
| Dành cho ai? | **Một đội/doanh nghiệp thật**, và về sau **bán dạng SaaS cho nhiều đội** |
| Quy mô | **Dưới 50 người dùng đồng thời** → giữ kiến trúc hiện tại (1 máy chủ, SQLite) |
| Ngân sách vận hành / tháng | **Hạ tầng 0 đồng**. **AI dùng Gemini trả phí** (chốt sau, xem §0.1 mục 2), giới hạn bởi trần chi phí ngày |
| Đưa vào MVP | Lịch hôm nay trên mobile; biên bản đóng ca; địa chỉ → toạ độ tự động; sửa thông tin ca (M11–M14 ở §5.1) |

### 0.1 Mâu thuẫn cần chốt (chưa được tự quyết)

1. ~~Bán SaaS với ngân sách 0 đồng.~~ **ĐÃ CHỐT (2026-11-22): HAI GIAI ĐOẠN.**
   - **Giai đoạn 1** (PRD này): một đội thật, hạ tầng miễn phí, MVP M1–M14, hạn **2 tuần → 2026-12-06**.
   - **Giai đoạn 2:** SaaS (giá theo đội, thanh toán, điều khoản, máy chủ trả phí), bắt đầu khi có khách trả tiền. Sẽ có PRD riêng.
   - Phân tích ban đầu (giữ để tham khảo):
   - SaaS thu tiền cần:
     - cổng thanh toán (Stripe đã tích hợp sẵn mã, nhưng có phí giao dịch và cần pháp nhân);
     - điều khoản sử dụng và chính sách quyền riêng tư;
     - máy chủ có cam kết hoạt động (gói miễn phí không cam kết);
     - sao lưu ra ngoài máy.
   - **Đề xuất:**
     - *Giai đoạn 1*: phục vụ **một đội thật** trên hạ tầng miễn phí.
     - *Giai đoạn 2* (SaaS): bắt đầu khi có khách trả tiền, dùng doanh thu trả hạ tầng. PRD này chỉ chốt MVP cho giai đoạn 1.
2. ~~Dữ liệu khách thật qua Gemini gói miễn phí.~~ **ĐÃ CHỐT (2026-11-22): dùng Gemini TRẢ PHÍ.**
   - Lý do: dữ liệu gửi tới gói trả phí không được Google dùng để cải thiện sản phẩm, phù hợp với dữ liệu cá nhân của khách thật. Cần đối chiếu lại điều khoản hiện hành của Google khi bật billing.
   - Hệ quả:
     - Ngân sách AI **không còn 0 đồng**. Chi phí bị chặn bởi trần đã có: **$2/ngày cho cả hệ thống, $0,20/người/ngày** (M10). Mức trần tối đa khoảng $60/tháng; mức dùng thực tế ở quy mô < 50 người dự kiến thấp hơn nhiều.
     - Khi triển khai: bật billing cho khoá Gemini; đặt `LLM_BILLING_TIER=paid` để trang Chi phí hiển thị tiền thật (gói free hiển thị $0); giữ hoặc điều chỉnh `LLM_DAILY_BUDGET_USD` / `LLM_USER_DAILY_BUDGET_USD`.
     - Không cần đổi mã: hệ thống đã hỗ trợ cả hai gói.

---

## 1. Vấn đề cần giải quyết

Kỹ thuật viên sửa chữa hiện trường (mạng cáp quang FTTH, thiết bị viễn thông) thường gặp:
1. Không biết **làm ca nào trước**: ca được giao qua điện thoại hoặc tin nhắn, không có lịch tuyến.
2. Tra **sai quy trình (SOP)** cho model thiết bị: tài liệu nằm rải rác, tìm lâu ngay tại nhà khách.
3. **Mất sóng thì không ghi được kết quả**, về văn phòng mới nhập lại, dễ sót và sai.
4. Trưởng đội **không thấy tiến độ** các ca; tổng đài/CRM không biết ca đã xử lý xong chưa.

FieldOps gom các việc này vào một chỗ: nhận ca (tay hoặc từ CRM/NMS) → xếp lịch tuyến → kỹ thuật viên làm checklist và ghi kết quả, kể cả khi offline → trợ lý AI tra SOP có dẫn nguồn → báo ngược trạng thái về CRM.

## 2. Người sử dụng

| Vai trò | Ai | Việc chính | Thiết bị |
|---|---|---|---|
| **Kỹ thuật viên** (`technician`) | Thợ đi hiện trường | Xem ca được giao, làm checklist, ghi kết quả, chụp ảnh, hỏi trợ lý | Điện thoại (app Expo hoặc web) |
| **Trưởng đội / điều phối** (`owner` của đội) | Người phân việc | Tạo đội, mời thành viên, tạo/giao ca, lập lịch tuyến, nạp SOP dùng chung, kết nối CRM/NMS | Máy tính (web) |
| **Quản trị hệ thống** (`admin`) | Người vận hành phần mềm | Quản lý tài khoản, xem chi phí AI và hạ tầng | Máy tính (web) |
| Hệ thống ngoài | CRM / NMS của nhà mạng | Gửi ticket/cảnh báo vào; nhận trạng thái ca | Webhook có chữ ký |

Đã xác nhận: một đội thật trước, SaaS sau (§0, §0.1).

## 3. Loại phần mềm

- **Web app** (Next.js): dành cho điều phối và quản trị, dùng được trên điện thoại.
- **Mobile app** (Expo, Android/iOS): dành cho kỹ thuật viên, có chế độ offline.
- **API** (FastAPI): dùng chung cho web, mobile và hệ thống ngoài (CRM/NMS, API công khai, widget nhúng).

## 4. Đăng nhập, phân quyền, dữ liệu

- **Đăng nhập:** email + mật khẩu (có thể bắt xác thực email) hoặc Google.
  - Phiên web lưu trong cookie httpOnly; mobile dùng token lưu trong SecureStore.
  - Đăng xuất thì thu hồi phiên trên mọi thiết bị.
- **Phân quyền:** hai tầng.
  1. Hệ thống: `admin` / `user`.
  2. Trong đội: `owner` / `technician`.
  - Mọi kiểm tra quyền làm ở backend: kỹ thuật viên không sửa được điều phối; người ngoài đội không xem được ca của đội.
- **Dữ liệu lưu trữ:**
  - Tài khoản; đội và thành viên.
  - Ca sửa, gồm địa chỉ và toạ độ khách hàng, checklist, nhật ký sự kiện, ảnh hiện trường đã xoá EXIF/GPS.
  - Tài liệu SOP và chỉ mục tìm kiếm.
  - Lịch sử hỏi đáp; chi phí AI.
  - CSDL dùng SQLite hoặc PostgreSQL. File lưu trên ổ đĩa hoặc volume.
- ⚠ **Dữ liệu cá nhân:** hệ thống lưu email người dùng, địa chỉ và toạ độ khách hàng, ảnh hiện trường. Vì vậy cần rà yêu cầu pháp lý Việt Nam về bảo vệ dữ liệu cá nhân (bước 7 — chưa làm).

## 5. Tính năng

### 5.1 MVP — đề xuất (đều ĐÃ CÓ trong phần mềm)

| Mã | Tính năng | Tiêu chí nghiệm thu (đo được) |
|---|---|---|
| M1 | Đăng ký / đăng nhập / đăng xuất | Đăng ký và đăng nhập thành công. Sai mật khẩu nhiều lần thì bị chặn tạm (HTTP 429). Người lạ không khoá được tài khoản của người khác. Đăng xuất thì token cũ không dùng lại được. |
| M2 | Đội và thành viên | Trưởng đội tạo đội, mời thành viên bằng email (không phân biệt hoa/thường). Thành viên chỉ thấy đội của mình. |
| M3 | Ca sửa | Tạo, xem, sửa trạng thái ca. Người ngoài đội nhận 404. Mỗi thay đổi được ghi vào nhật ký ca. |
| M4 | Checklist và ghi nhận tại hiện trường | Thêm bước, đánh dấu xong hoặc bị vướng, ghi kết quả. Gửi lại cùng một thao tác chỉ được áp dụng **đúng 1 lần**. |
| M5 | Offline | Khi mất mạng, thao tác được xếp hàng trên máy. Có mạng thì tự gửi, không mất và không trùng. |
| M6 | Tri thức đội (SOP) và trợ lý AI có dẫn nguồn | Trưởng đội nạp PDF/DOCX/TXT/ảnh. Trợ lý chỉ trả lời theo tài liệu đội, có nhãn nguồn 【E#】 kiểm tra được. Không có tài liệu thì từ chối trả lời. Lỗi AI thì trả thông báo rõ ràng (503), không treo. |
| M7 | Điều phối tuyến | Lập lịch cho đội trong vòng < 1 giây với ≤ 50 ca. Ca khẩn được xếp trước. Không vi phạm khung giờ hẹn hay ca làm. Trưởng đội sửa được rồi lưu. |
| M8 | Ảnh hiện trường | Chụp hoặc chọn ảnh trong ca. Server xoá vị trí GPS trong ảnh. Ảnh chỉ xem được qua link có hạn 15 phút. |
| M9 | Kết nối CRM/NMS | Ticket có chữ ký đúng thì tạo, cập nhật hoặc đóng ca. Sai chữ ký thì bị từ chối (401). Gửi lặp không tạo ca trùng. Trạng thái ca được báo ngược, có tự gửi lại. |
| M10 | Trần chi phí AI | Có giới hạn chi phí AI theo ngày cho cả hệ thống và từng người. Vượt thì từ chối có thông báo, không gọi AI. |
| **M11** | **Lịch hôm nay trên mobile** *(CHƯA CÓ — chủ dự án chọn đưa vào MVP)* | Kỹ thuật viên mở app thấy danh sách ca **được giao cho mình trong ngày**, đúng thứ tự tuyến và giờ dự kiến. Xem được khi offline (dữ liệu lần đồng bộ gần nhất). Trưởng đội đổi lịch thì app thấy lịch mới ở lần mở hoặc kéo-làm-mới tiếp theo. ⚠ *Thông báo đẩy* cần dịch vụ Expo Push (miễn phí): đề xuất làm sau M11 cơ bản. |
| **M12** | **Biên bản đóng ca** *(CHƯA CÓ)* | Không đóng được ca nếu thiếu **mã kết quả** (danh mục cố định, ví dụ: đã sửa / thay thiết bị / khách vắng / chuyển cấp), **ghi chú** và **chữ ký khách** (vẽ tay trên màn hình, lưu dạng ảnh như ảnh hiện trường). Biên bản xem lại được trong ca. Ca từ CRM thì webhook báo ngược kèm mã kết quả. Làm được khi offline và đồng bộ sau. |
| **M13** | **Địa chỉ → toạ độ tự động** *(CHƯA CÓ)* | Nhập địa chỉ khi tạo hoặc sửa ca thì có gợi ý toạ độ. Trưởng đội xác nhận hoặc sửa trên bản đồ trước khi lưu. Không tìm được thì báo rõ và vẫn cho nhập tay. **0 đồng:** dùng Nominatim (OpenStreetMap), tuân thủ giới hạn ≤ 1 yêu cầu/giây và có cache. Không gửi tên hay số điện thoại khách, chỉ gửi địa chỉ. |
| **M14** | **Sửa thông tin ca** *(ĐÃ LÀM 2026-11-22: `docs/design/M14_sua_thong_tin_ca.md` §8)* | Trưởng đội sửa tiêu đề, triệu chứng, model thiết bị, địa chỉ và thông tin bàn giao sau khi tạo ca. Kỹ thuật viên **không** sửa được các trường này. Mỗi lần sửa ghi nhật ký: ai sửa, trường nào, giá trị cũ → mới. Ca từ CRM: CRM cập nhật sau vẫn áp dụng theo quy tắc hiện có. |

### 5.2 Có thể bổ sung sau

| Tính năng | Lý do để sau |
|---|---|
| Thông báo đẩy cho lịch hôm nay | Làm sau M11 cơ bản (Expo Push, miễn phí) |
| Cam kết thời gian xử lý (SLA), cảnh báo ca sắp trễ | Chưa có yêu cầu SLA cụ thể |
| Tính giá theo đội, thanh toán, điều khoản SaaS | Giai đoạn 2 (SaaS), xem §0.1 |
| Trợ lý tài liệu cá nhân, widget nhúng, API công khai, pipeline dữ liệu (Medallion/Airflow) | Đã làm nhưng không phải lõi của bài toán hiện trường |

Đã xác nhận: 4 tính năng được chuyển lên MVP thành M11–M14.

## 6. Quy mô, ngân sách, thời gian

| Hạng mục | Thiết kế hiện tại chịu được | ⚠ Cần bạn cho biết |
|---|---|---|
| Số người dùng | Một VM nhỏ (2 OCPU / 12 GB, gói miễn phí của Oracle), SQLite, 1 tiến trình | ✅ **< 50 đồng thời**: đủ, không đổi kiến trúc |
| Ngân sách hạ tầng | Oracle Always Free (khuyến nghị: Render free mất dữ liệu khi ngủ hoặc deploy lại) | ✅ **0 đồng**. Tên miền: dùng subdomain miễn phí (DuckDNS, đã hỗ trợ) |
| Ngân sách AI | Mặc định $2/ngày cho cả hệ thống, $0,20/người/ngày | ✅ **Gemini trả phí**, trần tối đa khoảng $60/tháng (§0.1 mục 2) |
| Thời gian | — | ✅ **Giai đoạn 1 trong 2 tuần (hạn 2026-12-06)** |

## 7. Tiêu chí nghiệm thu chung (ngoài từng tính năng)

1. **Bảo mật:**
   - Không có khoá bí mật trong mã nguồn.
   - Production từ chối khởi động nếu cấu hình nguy hiểm.
   - Có header bảo mật và chống CSRF.
   - Không lộ chi tiết lỗi ra người dùng.
2. **Kiểm thử:** toàn bộ test tự động đạt (hiện 613 test backend + 23 test client), lint sạch, build thành công.
3. **Vận hành:**
   - Chạy được bằng `docker compose up --build` với HTTPS.
   - Có sao lưu hằng ngày và **đã thử khôi phục thành công**. ⚠ Việc thử khôi phục chưa làm.
   - Có rollback khi deploy lỗi.
4. **Giao diện:** dùng được trên điện thoại rộng 375 px. Có màn lỗi tiếng Việt. Thông báo tiếng Việt.
5. **Hiệu năng:** ⚠ cần chốt con số mục tiêu, ví dụ trang ca mở < 2 giây, trợ lý trả lời < 15 giây, chịu được N người dùng đồng thời. Hiện chưa đo.

## 8. Ngoài phạm vi

- Trợ lý **không** tự điều khiển hay thay đổi cấu hình thiết bị; kỹ thuật viên chịu trách nhiệm thao tác.
- Không dẫn đường như bản đồ thật: thời gian di chuyển là ước lượng (đường chim bay × 1,35).
- Chưa có adapter riêng cho một CRM/NMS cụ thể; hệ thống ngoài phải gửi theo định dạng webhook chung.

## 9. Kế hoạch giai đoạn 1 (2 tuần, hạn 2026-12-06)

Các tính năng mới làm theo **thứ tự giá trị trên công sức**. Tính năng nào không kịp thì lùi sang sau hạn; không cắt bớt chất lượng hay kiểm thử.

| Thứ tự | Việc | Lý do xếp ở vị trí này |
|---|---|---|
| 1 | **M14** Sửa thông tin ca | Nhỏ, chỉ ở backend và web, dùng lại cơ chế nhật ký sẵn có. Gỡ một vướng mắc hằng ngày. |
| 2 | **M11** Lịch hôm nay trên mobile | API lịch đã có (`/schedule`). Chỉ thêm màn hình mobile và cache offline. Giá trị lớn nhất cho thợ. |
| 3 | **M12** Biên bản đóng ca | Cần thay đổi CSDL (migration), màn hình chữ ký, offline và báo ngược CRM. Công sức lớn nhất. |
| 4 | **M13** Địa chỉ → toạ độ | Gọi dịch vụ ngoài (Nominatim) và chịu giới hạn tốc độ của nó. Thiếu thì vẫn nhập tay được, nên rủi ro thấp nhất nếu lùi. |
| Song song | Bước 7–8 còn thiếu | Rà pháp lý dữ liệu cá nhân Việt Nam; **thử khôi phục từ bản sao lưu**; chốt và đo các con số hiệu năng (§7 mục 5). |

Mỗi tính năng đi đủ các bước của quy trình: thiết kế (bước 2–4) → **chủ dự án xác nhận** → code → kiểm thử theo tiêu chí ở §5.1 → báo cáo → commit.
