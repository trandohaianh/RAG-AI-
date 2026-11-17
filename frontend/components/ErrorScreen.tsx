"use client";

import { useEffect } from "react";

type Props = {
  title: string;
  message: string;
  /** Thử dựng lại đoạn giao diện lỗi (Next error boundary). */
  onRetry?: () => void;
  digest?: string;
};

/**
 * Màn hình lỗi dùng chung cho error boundary của Next. Trước đây lỗi render hay chunk JS tải
 * hỏng sau một lần deploy hiện màn "Application error" trống trơn, không có đường quay lại —
 * kỹ thuật viên ngoài hiện trường kẹt luôn. Dữ liệu chờ đồng bộ (outbox offline) nằm trong
 * IndexedDB nên không bị ảnh hưởng khi tải lại trang.
 */
export default function ErrorScreen({ title, message, onRetry, digest }: Props) {
  useEffect(() => {
    if (digest) console.error("FieldOps error", digest);
  }, [digest]);

  const retry = () => {
    // Chunk hỏng sau deploy: dựng lại không đủ, phải tải lại trang để lấy bản mới.
    if (onRetry) onRetry();
    else window.location.reload();
  };

  return (
    <div role="alert" className="min-h-[60vh] flex items-center justify-center p-6">
      <div className="max-w-md w-full rounded-xl border border-slate-200 bg-white p-6 text-center shadow-xs">
        <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
        <p className="mt-2 text-sm text-slate-600">{message}</p>
        <p className="mt-1 text-xs text-slate-400">
          Các thao tác chưa gửi (chế độ offline) vẫn được giữ trên máy.
        </p>
        <div className="mt-5 flex flex-wrap justify-center gap-2">
          <button
            type="button"
            onClick={retry}
            className="rounded-full bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >
            Thử lại
          </button>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Tải lại trang
          </button>
          <a
            href="/field"
            className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Về Ca hiện trường
          </a>
        </div>
        {digest && <p className="mt-4 text-[11px] text-slate-400">Mã lỗi: {digest}</p>}
      </div>
    </div>
  );
}
