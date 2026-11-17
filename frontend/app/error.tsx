"use client";

import ErrorScreen from "@/components/ErrorScreen";

export default function RootError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <ErrorScreen
      title="Trang gặp lỗi"
      message="Đã có lỗi khi hiển thị trang này. Bấm Thử lại; nếu vẫn lỗi, hãy tải lại trang."
      onRetry={reset}
      digest={error.digest}
    />
  );
}
