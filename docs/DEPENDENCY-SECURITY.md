# Dependency và bảo mật — 22/10/2026

## Bản nâng cấp đã áp dụng

| Thành phần | Phiên bản đã kiểm chứng |
|---|---|
| Backend HTTP | FastAPI 0.159.1, Starlette 1.9.0, python-multipart 0.0.32 |
| JWT | PyJWT 2.15.1 thay python-jose; môi trường mới không có python-jose/ecdsa |
| PDF / cấu hình / test | pypdf 6.21.2, python-dotenv 1.2.4, pytest 9.1.1 |
| Công cụ cài Python | pip 26.2.2 trong môi trường kiểm chứng và Dockerfile backend |
| Web | Next.js 16.4.2, React/React DOM 19.3.1, Tailwind/PostCSS plugin 4.3.3 |
| Web HTTP / ảnh | Axios 1.20.0, follow-redirects 1.16.1, sharp 0.35.5 |
| Mobile | Expo 57.0.31, metro-runtime 57.0.17, document-picker 57.0.3, linear-gradient 57.0.2, expo-font 57.0.4 |
| Mobile React / native | Giữ React 19.2.3 và React Native 0.86.3 theo Expo 57 |

JWT tiếp tục xác minh thuật toán cấu hình, bắt buộc exp/sub và kiểm tra purpose/token_version. Token HS256 hợp lệ đã có các claim của Đợt 1 vẫn dùng được đến hạn; token cũ trước Đợt 1 vẫn cần đăng nhập lại. Password reset vẫn dùng một lần và thu hồi token cũ. Import contract đã chuyển từ jose.jwt sang jwt.

Production/staging và môi trường không nhận diện không tự cho phép CORS localhost/LAN. Đặt CORS_ORIGINS bằng origin web chính xác; CORS_ORIGIN_REGEX chỉ bật khi được cấu hình. Môi trường phát triển giữ private IPv4 và loopback/ULA/link-local IPv6; không mặc định cho phép IPv6 công khai.

Web chuyển sang PostCSS plugin của Tailwind 4, giữ cấu hình màu/radius hiện có và điều chỉnh các utility đổi tên. Browser tối thiểu theo [Tailwind 4](https://tailwindcss.com/docs/upgrade-guide): Chrome 111+, Safari 16.4+, Firefox 128+. Bảng màu/gradient mặc định có thể khác nhẹ; đã đối chiếu ảnh home/login trước và sau. Không cam kết giao diện dark hoàn chỉnh ở các vùng đang dùng màu cố định.

Docker/CI web dùng Node 22; Next 16 bỏ next lint nên package dùng typecheck tường minh. CI chạy typecheck, build, audit web mức moderate trở lên và regression client. Đây chưa phải kết quả chạy GitHub Actions trên Linux. Dockerfile dùng npm ci, không tự chuyển sang npm install khi lockfile lỗi. [Hướng dẫn nâng cấp Next 16](https://nextjs.org/docs/app/guides/upgrading/version-16) và [bản tin bảo mật 14/10](https://nextjs.org/blog/nextjs-security-update-october-14-2026) là căn cứ lựa chọn phiên bản web.

## Kết quả kiểm toán

Audit là ảnh chụp dữ liệu advisory tại thời điểm chạy; không chứng minh ứng dụng không còn mọi lỗ hổng. Không dùng ignore-vuln hoặc audit fix --force để làm xanh báo cáo.

| Phạm vi | Trước | Sau |
|---|---|---|
| Backend, pip-audit toàn bộ site-packages | 84 cặp package–advisory khác nhau ở 9 package | 4 cặp ở chromadb 1.5.9 |
| Web, npm audit toàn bộ lockfile | 15 package bị đánh dấu, gồm 1 critical | 0; audit omit=dev cũng 0 |
| Mobile, lần quét thành công sau cập nhật SDK/dependency | Baseline 26 package, gồm 1 critical | 15 high, 0 critical; quy về 2 advisory gốc |

Backend dùng pip-audit 2.10.1 ở venv công cụ riêng. Báo cáo gốc có bản ghi trùng: trước 145 bản ghi trên 137 package được quét; sau 5 bản ghi trên 135 package, tương ứng 84 và 4 cặp khác nhau. Không cộng bản ghi trùng thành CVE riêng. Số npm audit là package/metavulnerability, không phải số CVE riêng.

Baseline web đã quan sát thành công trên console; lần lưu raw baseline sau đó bị timeout. Artifact baseline-summary.json ghi rõ giới hạn này. Audit web cuối đầy đủ và production đều có raw JSON hợp lệ. Endpoint npm audit thỉnh thoảng timeout; báo cáo lỗi được giữ riêng và không diễn giải thành zero. Chi tiết và hash bằng chứng được lưu tại [audit-summary.json](audit-summary.json).

Sau khi khóa expo-font 57.0.4, các lần npm audit trên lockfile/cây cài đặt cuối bị timeout; không có báo cáo CLI thành công mới để khẳng định lại tổng metavulnerability 15 cho chính lockfile này. Đã xác minh thay thế bằng cách gửi đầy đủ 457 tên package cùng mọi phiên bản trong lockfile cuối tới API advisory chính thức của npm: kết quả chỉ có hai advisory braces/node-forge nêu dưới. Phép kiểm tra này xác minh advisory gốc, không tính lại tổng package/metavulnerability của npm.

### ChromaDB: 4 advisory chưa có phiên bản vá

- [CVE-2026-45829 / GHSA-f4j7-r4q5-qw2c](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c): tạo collection trên HTTP server có thể kích hoạt mã model từ xa.
- [CVE-2026-45833 / GHSA-36p7-vc44-83pf](https://github.com/advisories/GHSA-36p7-vc44-83pf): cập nhật collection/trust_remote_code.
- [CVE-2026-45831 / GHSA-xph7-9rjv-w5fr](https://github.com/advisories/GHSA-xph7-9rjv-w5fr): phạm vi quyền của SimpleRBACAuthorizationProvider.
- [CVE-2026-45830 / GHSA-2wm9-hf6c-p5cr](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr): truy cập collection khác tenant.

PyPI hiện chưa có bản mới hơn 1.5.9. Repo dùng PersistentClient trong process, collection user/workspace do backend chọn; Compose không chạy Chroma HTTP server và ứng dụng không mount API quản trị Chroma. Vì vậy đường HTTP được mô tả trong advisory chưa được expose ở cấu hình đã kiểm tra; đây là đánh giá đường gọi, không phải chứng nhận miễn nhiễm hay audit sạch. Không mở Chroma HTTP trực tiếp ra mạng hoặc cho client điều khiển cấu hình embedding/model. Cần theo dõi bản vá hoặc đánh giá thay kho vector nếu triển khai cần Chroma server nhiều tenant.

### Mobile: hai advisory gốc chưa có phiên bản vá

- [braces 3.0.3 / GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm): mẫu glob lồng sâu có thể gây stack overflow. Đường phụ thuộc đi qua micromatch → Metro/Expo toolchain.
- [node-forge 1.4.0 / GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv): kiểm tra chữ ký RSA PKCS#1 v1.5 không chặt. Expo CLI và @expo/code-signing-certificates dùng thư viện này; mã kiểm tra certificate có gọi verify.

Đã xác minh registry vẫn phát hành mới nhất lần lượt 3.0.3 và 1.4.0. npm gợi ý hạ Expo 44/RN 0.72 để tránh một số chuỗi phụ thuộc; không áp dụng vì phá tương thích SDK hiện tại. Shell-quote đã lên 1.12.0, hết advisory critical trong lần audit thành công.

Đã kiểm tra source map của bundle Android/iOS/web: không thấy source thuộc braces/node-forge trong 918/923/635 source tương ứng. Bằng chứng này chỉ áp dụng ba bundle vừa export; không loại bỏ rủi ro với máy phát triển, CI hoặc công cụ ký. npm audit omit=dev cũng không tách được toolchain chính xác vì Expo được khai báo trong dependencies. Chỉ chạy Metro với dự án, glob và chứng chỉ đáng tin cậy; dùng expo start --localhost nếu không cần thiết bị LAN, không expose dev server công khai.

## Xác minh cục bộ

- Windows/Python 3.12.4: 272 test backend qua, gồm 10 test PostgreSQL 16; không failure/error/skip. Có một warning dự kiến khi tạo token HS384 để kiểm tra bị từ chối.
- JWT: exp/sub bắt buộc, expiry, sub sai kiểu, unsigned, sai thuật toán/chữ ký, tương thích typed HS256 độc lập thư viện; reset/revocation/race tests cũ vẫn qua.
- PDF: parser thật đọc hai trang và giữ source/page; file hỏng và có mật khẩu bị từ chối trước indexing. Các test rollback/crash/recovery vẫn qua.
- Dependency check: 28 package đúng ràng buộc, pip check và 18 nhóm import qua.
- Client: 14/14 regression qua; web typecheck/build và npm ls không có peer dependency lỗi.
- Chrome: 12/12 tình huống home/login/field × desktop/mobile × light/dark qua; HTTP 200, không page error/tràn ngang, class theme được áp dụng; submit login hiển thị lỗi API giả lập. Có đối chiếu ảnh home/login với baseline 8 tình huống. API/đăng nhập/workspace được giả lập, chưa phải E2E production.
- Mobile: Expo install --check hợp lệ, TypeScript qua, export JavaScript/Hermes Android/iOS và web thành công. Chưa build native binary, chạy simulator hay kiểm tra offline trên thiết bị thật. Thay native package/plugin expo-font cần tạo lại binary phù hợp trước phát hành.
- BM25 fixture tổng hợp 16/16 hoàn tất; validate-only RAG template hợp lệ, không gọi model/API trả phí.
- Compose config và git diff --check hợp lệ. Chưa build Docker image mới hoặc chạy CI Linux. Không triển khai cloud, thay DB ứng dụng hoặc chạy migration trên dữ liệu thật.

Môi trường kiểm chứng mới: backend/.pytest_security_runtime_tmp/.venv. Venv Đợt 3 và .venv cũ được giữ lại để đối chiếu; không dùng chúng để khởi động checkout mới. Khi phát hành, tạo môi trường mới từ requirements, nâng pip, rồi chạy check_dependencies.py; cài chồng requirements không tự gỡ python-jose/ecdsa tồn dư.

## Chạy lại kiểm tra

Từ thư mục gốc, dùng interpreter của môi trường mới:

```powershell
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/check_dependencies.py
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B -m pytest tests/ -q -p no:cacheprovider
node --test tests/client_state.test.cjs
```

PostgreSQL tests cần FIELDOPS_TEST_POSTGRES_URL trỏ DB kiểm thử riêng; thiếu biến này các test tích hợp sẽ skip. Không dùng URL production.

Trong frontend: npm ci, npm run typecheck, npm run build, npm audit và npm audit --omit=dev. Trong mobile: npm ci, expo install --check qua CLI cục bộ, TypeScript và export các nền tảng. Mobile còn advisory thì audit exit 1 là kết quả có lỗ hổng; lỗi mạng có message/error và thiếu metadata phải xử lý riêng.

Pip-audit chạy ở venv công cụ riêng, trỏ --path đến Lib/site-packages của runtime trên Windows (Linux dùng đường site-packages tương ứng):

```powershell
.\backend\.pytest_security_tmp\tooling\Scripts\python.exe -B -m pip_audit --path backend/.pytest_security_runtime_tmp/.venv/Lib/site-packages --strict --progress-spinner off --format json
```

Raw reports, JUnit, screenshots và bundle/source maps ở các thư mục backend/.pytest*_tmp được gitignore; audit-summary.json lưu số đếm và hash báo cáo để đối chiếu. Tooling audit không nằm trong dependency runtime của ứng dụng.

## Ưu tiên tiếp theo

1. Theo dõi và xác minh lại các advisory Chroma/Metro/Forge; kiểm tra lại đường gọi trước khi đổi cách triển khai hoặc chấp nhận dữ liệu cấu hình/chứng chỉ từ bên ngoài.
2. Tạo bộ tài liệu/query RAG được người phụ trách xác nhận, chạy retrieval rồi đánh giá correctness/safety/refusal. Benchmark tổng hợp và build/test hiện tại chưa chứng minh chất lượng trả lời thực tế.
3. Chạy staging trên bản sao dữ liệu: migration, backup/restore, auth/billing/upload, offline/reconnect trên thiết bị thật; đo độ trễ, lỗi và quota trước khi mở rộng pilot.
4. Sau các gate trên, giải quyết commit/lineage giữa Medallion và kho tài liệu/index; hiện chưa cùng transaction nguyên tử.
