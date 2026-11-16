"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import api, { getApiErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import AuthBackdrop, { darkInputClass } from "@/components/AuthBackdrop";
import { LogoMark } from "@/components/Logo";
import GoogleSignInButton from "@/components/GoogleSignInButton";

export default function RegisterPage() {
  const router = useRouter();
  const { login, user, loading: authLoading } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!authLoading && user) router.replace("/chat");
  }, [authLoading, user, router]);

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
    setLoading(true);
    try {
      const res = await api.post("/auth/register", { email, password });
      login(res.data.user);
      router.replace("/chat");
    } catch (err: unknown) {
      setError(getApiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthBackdrop>
      <div className="w-full max-w-md text-center">
        <LogoMark size={56} className="mx-auto shadow-lg shadow-black/40" />

        <h1 className="mt-6 text-3xl font-semibold tracking-tight">Tạo tài khoản</h1>
        <p className="mt-2 text-sm text-white/50">
          Đã có tài khoản?{" "}
          <Link href="/login" className="font-medium text-white hover:underline">
            Đăng nhập
          </Link>
          .
        </p>

        <form onSubmit={onSubmit} className="mt-8 space-y-3 text-left">
          <GoogleSignInButton onError={setError} />
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

          <div>
            <label className="mb-1.5 block text-xs font-medium text-white/60">Mật khẩu</label>
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
            disabled={loading}
            className="brand-gradient mt-2 w-full rounded-full py-3 text-sm font-semibold text-white shadow-lg shadow-black/40 transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {loading ? "Đang tạo tài khoản..." : "Đăng ký"}
          </button>
        </form>

        <p className="mt-8 text-xs text-white/30">
          Bằng việc đăng ký, bạn đồng ý với các điều khoản sử dụng của hệ thống.
        </p>
      </div>
    </AuthBackdrop>
  );
}
