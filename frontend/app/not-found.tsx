import Link from "next/link";

export default function NotFound() {
  return (
    <div className="min-h-[60vh] flex items-center justify-center p-6">
      <div className="max-w-md w-full rounded-xl border border-slate-200 bg-white p-6 text-center shadow-xs">
        <h1 className="text-lg font-semibold text-slate-900">Không tìm thấy trang</h1>
        <p className="mt-2 text-sm text-slate-600">Đường dẫn không tồn tại hoặc đã được đổi.</p>
        <Link
          href="/field"
          className="mt-5 inline-block rounded-full bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          Về Ca hiện trường
        </Link>
      </div>
    </div>
  );
}
