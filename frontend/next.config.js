/** @type {import('next').NextConfig} */

// Content-Security-Policy (có nonce theo từng request) được đặt trong proxy.ts.

/**
 * API cùng origin: khi đặt API_PROXY_TARGET (vd. host của service API trên Render), mọi request
 * /api/* được Next chuyển tiếp tới API. Trình duyệt chỉ nói chuyện với domain web, nên cookie
 * phiên httpOnly là first-party. Web và API ở hai subdomain onrender.com là HAI site khác nhau
 * (onrender.com nằm trong Public Suffix List): cookie SameSite=Lax không bao giờ được gửi và
 * người dùng bị đẩy về /login sau mỗi lần đăng nhập. Rewrites được tính lúc build.
 */
function apiRewrites() {
  const raw = (process.env.API_PROXY_TARGET || "").trim().replace(/\/+$/, "");
  if (!raw) return [];
  const base = /^https?:\/\//.test(raw) ? raw : `https://${raw}`;
  return [{ source: "/api/:path*", destination: `${base}/:path*` }];
}

const nextConfig = {
  output: "standalone",
  async rewrites() {
    return apiRewrites();
  },
};

module.exports = nextConfig;
