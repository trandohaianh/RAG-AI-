"use client";

import ErrorScreen from "@/components/ErrorScreen";

// Bắt lỗi bên trong khung ứng dụng: thanh bên vẫn còn để chuyển sang mục khác.
export default function AppSectionError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <ErrorScreen
      title="Mục này gặp lỗi"
      message="Không hiển thị được nội dung. Bấm Thử lại, hoặc chọn mục khác ở thanh bên."
      onRetry={reset}
      digest={error.digest}
    />
  );
}
