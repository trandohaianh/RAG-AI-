"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import api, { getApiErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import AuthBackdrop, { darkInputClass } from "@/components/AuthBackdrop";
import { LogoMark } from "@/components/Logo";
import GoogleSignInButton from "@/components/GoogleSignInButton";

export default function LoginPage() {
  const router = useRouter();
  const { login, user, loading: authLoading } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!authLoading && user) router.replace("/chat");
  }, [authLoading, user, router]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const res = await api.post("/auth/login", { email, password });
      login(res.data.user);
      router.replace("/chat");
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

        <h1 className="mt-6 text-3xl font-semibold tracking-tight">Đăng nhập FieldOps</h1>
        <p className="mt-2 text-sm text-white/50">
          Chưa có tài khoản?{" "}
          <Link href="/register" className="font-medium text-white hover:underline">
            Đăng ký
          </Link>
          .
        </p>

        <form onSubmit={onSubmit} className="mt-8 space-y-3 text-left">
          <GoogleSignInButton onError={setError} />
          {error && (
            <p className="rounded-2xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>
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

          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <label className="text-xs font-medium text-white/60">Mật khẩu</label>
              <Link href="/forgot-password" className="text-xs font-medium text-white/50 hover:text-white">
                Quên mật khẩu?
              </Link>
            </div>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
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
            {submitting ? "Đang đăng nhập..." : "Đăng nhập"}
          </button>
        </form>

        <p className="mt-8 text-xs text-white/30">
          Xem{" "}
          <Link href="/privacy" className="underline hover:text-white/60">
            Chính sách quyền riêng tư
          </Link>
          .
        </p>
      </div>
    </AuthBackdrop>
  );
}
