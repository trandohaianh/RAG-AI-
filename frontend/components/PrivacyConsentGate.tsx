"use client";

import { useState } from "react";
import Link from "next/link";
import { ShieldCheck } from "lucide-react";
import api, { getApiErrorMessage } from "@/lib/api";
import { useAuth, type User } from "@/lib/auth";

/**
 * Người dùng chưa đồng ý phiên bản Chính sách quyền riêng tư hiện hành (tài khoản cũ, đăng nhập
 * Google lần đầu, hoặc chính sách vừa đổi) thì phải đồng ý trước khi dùng tiếp.
 */
export default function PrivacyConsentGate() {
  const { user, login, logout } = useAuth();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!user?.privacy_version_current || user.privacy_version === user.privacy_version_current) return null;
  const version = user.privacy_version_current;

  async function accept() {
    setSaving(true);
    setError(null);
    try {
      const res = await api.post<User>("/auth/me/consent", { version });
      login(res.data);
    } catch (err) {
      setError(getApiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[2000] flex items-center justify-center bg-slate-900/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="privacy-gate-title"
    >
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
        <ShieldCheck className="h-8 w-8 text-primary" />
        <h2 id="privacy-gate-title" className="mt-3 text-lg font-semibold text-slate-900">
          Chính sách quyền riêng tư
        </h2>
        <p className="mt-2 text-sm text-slate-600">
          {user.privacy_version
            ? "Chính sách quyền riêng tư vừa được cập nhật."
            : "Trước khi tiếp tục, vui lòng đọc Chính sách quyền riêng tư."}{" "}
          Chính sách giải thích FieldOps thu thập dữ liệu gì, gửi cho ai (có máy chủ ở nước ngoài) và cách bạn tải hoặc
          xoá dữ liệu của mình.
        </p>
        <Link
          href="/privacy"
          target="_blank"
          className="mt-3 inline-block text-sm font-medium text-primary hover:underline"
        >
          Đọc chính sách (phiên bản {version}) ↗
        </Link>
        {error && <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            onClick={logout}
            className="rounded-md px-3 py-2 text-sm text-slate-600 hover:bg-slate-100"
          >
            Đăng xuất
          </button>
          <button
            type="button"
            onClick={accept}
            disabled={saving}
            className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50"
          >
            {saving ? "Đang lưu..." : "Tôi đã đọc và đồng ý"}
          </button>
        </div>
      </div>
    </div>
  );
}
