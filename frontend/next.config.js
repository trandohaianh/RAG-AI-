/** @type {import('next').NextConfig} */

// Content-Security-Policy cho console. Token đăng nhập nằm trong localStorage, nên một lỗi
// XSS bất kỳ là lộ phiên: CSP giới hạn script/kết nối/khung về đúng các nguồn ứng dụng dùng.
// App Router chèn script inline khi hydrate nên vẫn cần 'unsafe-inline' cho script (chưa dùng
// nonce); phần chặn được: script từ domain lạ, gửi dữ liệu ra domain lạ, <object>, <base>,
// nhúng trang vào site khác (clickjacking).
function apiOrigin() {
  try {
    return new URL(process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").origin;
  } catch {
    return "";
  }
}

function csp({ embed }) {
  const api = apiOrigin();
  return [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/client",
    "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style",
    // Bản đồ tuyến (OpenStreetMap tile), ảnh hiện trường qua URL ký của API, avatar Google.
    "img-src 'self' data: blob: https:",
    "font-src 'self' data:",
    `connect-src 'self' ${api} https://accounts.google.com/gsi/`.trim(),
    "frame-src https://accounts.google.com/gsi/",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    // Widget /embed được nhúng trên website khách hàng; mọi trang khác chỉ cùng origin.
    embed ? "frame-ancestors *" : "frame-ancestors 'self'",
  ].join("; ");
}

const nextConfig = {
  output: "standalone",
  async headers() {
    // Chế độ dev cần eval/websocket cho hot reload: chỉ bật CSP ở bản build production.
    if (process.env.NODE_ENV !== "production") return [];
    return [
      { source: "/((?!embed).*)", headers: [{ key: "Content-Security-Policy", value: csp({ embed: false }) }] },
      { source: "/embed", headers: [{ key: "Content-Security-Policy", value: csp({ embed: true }) }] },
    ];
  },
};

module.exports = nextConfig;
