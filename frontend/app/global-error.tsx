"use client";

import "./globals.css";
import ErrorScreen from "@/components/ErrorScreen";

// Lỗi ở chính layout gốc: phải tự dựng <html>/<body> vì layout không render được.
export default function GlobalError({ error }: { error: Error & { digest?: string } }) {
  return (
    <html lang="vi">
      <body className="font-sans antialiased bg-slate-50">
        <ErrorScreen
          title="FieldOps gặp lỗi"
          message="Ứng dụng không tải được. Tải lại trang để lấy phiên bản mới nhất."
          digest={error.digest}
        />
      </body>
    </html>
  );
}
