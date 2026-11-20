"use client";

import { useState } from "react";
import Link from "next/link";
import { Mail } from "lucide-react";
import api, { getApiErrorMessage } from "@/lib/api";
import AuthBackdrop, { darkInputClass } from "@/components/AuthBackdrop";
import { LogoMark } from "@/components/Logo";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await api.post("/auth/forgot-password", { email });
      setSent(true);
    } catch (err: unknown) {
      setError(getApiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthBackdrop>
      <div className="w-full max-w-md text-center">
        <LogoMark size={56} className="mx-auto shadow-lg shadow-black/40" />

        <h1 className="mt-6 text-3xl font-semibold tracking-tight">Quên mật khẩu?</h1>
        <p className="mt-2 text-sm text-white/50">Nhập email đã đăng ký — chúng tôi sẽ gửi link đặt lại mật khẩu.</p>

        {sent ? (
          <div className="mt-8 rounded-xl border border-emerald-500/25 bg-emerald-500/10 px-4 py-5 text-left">
            <div className="flex items-start gap-3">
              <Mail className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300" />
              <p className="text-sm text-emerald-200">
                Nếu <strong>{email}</strong> tồn tại trong hệ thống, một email chứa link đặt lại mật khẩu đã được gửi
                tới. Kiểm tra cả mục Spam nếu chưa thấy sau vài phút.
              </p>
            </div>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="mt-8 space-y-3 text-left">
            {error && (
              <p className="rounded-2xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                {error}
              </p>
            )}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-white/60">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="ban@example.com"
                className={darkInputClass()}
              />
            </div>
            <button
              type="submit"
              disabled={submitting}
              className="brand-gradient mt-2 w-full rounded-full py-3 text-sm font-semibold text-white shadow-lg shadow-black/40 transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? "Đang gửi..." : "Gửi link đặt lại mật khẩu"}
            </button>
          </form>
        )}

        <p className="mt-8 text-sm text-white/50">
          Nhớ mật khẩu rồi?{" "}
          <Link href="/login" className="font-medium text-white hover:underline">
            Đăng nhập
          </Link>
        </p>
      </div>
    </AuthBackdrop>
  );
}
