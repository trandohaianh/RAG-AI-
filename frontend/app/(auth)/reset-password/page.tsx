"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import api, { getApiErrorMessage } from "@/lib/api";
import AuthBackdrop, { darkInputClass } from "@/components/AuthBackdrop";
import { LogoMark } from "@/components/Logo";

function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (password !== confirm) {
      setError("Mật khẩu xác nhận không khớp");
      return;
    }
    if (password.length < 6) {
      setError("Mật khẩu tối thiểu 6 ký tự");
      return;
    }
    setSubmitting(true);
    try {
      await api.post("/auth/reset-password", { token, password });
      setDone(true);
      setTimeout(() => router.replace("/login"), 2000);
    } catch (err: unknown) {
      setError(getApiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="w-full max-w-md text-center">
      <LogoMark size={56} className="mx-auto shadow-lg shadow-black/40" />

      <h1 className="mt-6 text-3xl font-semibold tracking-tight">Đặt lại mật khẩu</h1>

      {!token ? (
        <p className="mt-6 rounded-2xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          Link không hợp lệ — thiếu token. Yêu cầu lại từ trang{" "}
          <Link href="/forgot-password" className="underline">
            Quên mật khẩu
          </Link>
          .
        </p>
      ) : done ? (
        <p className="mt-6 rounded-xl border border-emerald-500/25 bg-emerald-500/10 px-4 py-4 text-sm text-emerald-200">
          Đặt lại mật khẩu thành công — đang chuyển tới trang đăng nhập...
        </p>
      ) : (
        <form onSubmit={onSubmit} className="mt-8 space-y-3 text-left">
          {error && (
            <p className="rounded-2xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>
          )}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-white/60">Mật khẩu mới</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              placeholder="Tối thiểu 6 ký tự"
              className={darkInputClass()}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-white/60">Xác nhận mật khẩu</label>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
              placeholder="••••••••"
              className={darkInputClass()}
            />
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="brand-gradient mt-2 w-full rounded-full py-3 text-sm font-semibold text-white shadow-lg shadow-black/40 transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "Đang lưu..." : "Đặt lại mật khẩu"}
          </button>
        </form>
      )}
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <AuthBackdrop>
      <Suspense fallback={null}>
        <ResetPasswordForm />
      </Suspense>
    </AuthBackdrop>
  );
}
