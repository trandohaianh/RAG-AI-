import { NextResponse, type NextRequest } from "next/server";

/**
 * Content-Security-Policy với nonce cho từng request (Next 16 "proxy", trước đây là middleware).
 *
 * script-src chỉ chấp nhận script mang nonce của request này ('strict-dynamic' cho phép các
 * chunk mà chính script đó nạp, kể cả Google Sign-In). Script inline do XSS chèn vào không có
 * nonce nên bị chặn — khác với bản trước phải mở 'unsafe-inline'. Next tự gắn nonce vào script
 * của nó khi đọc thấy nonce trong header CSP của request (trang phải render động: xem
 * app/layout.tsx).
 *
 * style-src vẫn cho 'unsafe-inline': thuộc tính style của React/Leaflet không mang nonce được,
 * và CSS không chạy được mã.
 */
function apiOrigin(): string {
  try {
    return new URL(process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").origin;
  } catch {
    return "";
  }
}

function buildCsp(nonce: string, { embed = false, dev = false } = {}): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' https://accounts.google.com/gsi/client${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style",
    "img-src 'self' data: blob: https:",
    "font-src 'self' data:",
    `connect-src 'self' ${apiOrigin()} https://accounts.google.com/gsi/${dev ? " ws:" : ""}`,
    "frame-src https://accounts.google.com/gsi/",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    // Widget /embed được nhúng trên website khách hàng; mọi trang khác chỉ cùng origin.
    embed ? "frame-ancestors *" : "frame-ancestors 'self'",
  ].join("; ");
}

export function proxy(request: NextRequest) {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  const nonce = btoa(String.fromCharCode(...bytes));
  const csp = buildCsp(nonce, {
    embed: request.nextUrl.pathname === "/embed" || request.nextUrl.pathname.startsWith("/embed/"),
    dev: process.env.NODE_ENV === "development",
  });
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.ico|icon.png).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
