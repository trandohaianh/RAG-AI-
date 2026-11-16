"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import api, { getApiErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import AuthBackdrop from "@/components/AuthBackdrop";
import { LogoMark } from "@/components/Logo";

function VerifyEmailInner() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const { user, refreshUser } = useAuth();
  const [state, setState] = useState<"working" | "ok" | "error">(token ? "working" : "error");
  const [message, setMessage] = useState(token ? "" : "Thiếu mã xác thực trong đường link.");
  const started = useRef(false);

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true;
    api
      .post("/auth/verify-email", { token })
      .then(async () => {
        setState("ok");
        await refreshUser();
      })
      .catch((err) => {
        setState("error");
        setMessage(getApiErrorMessage(err));
      });
  }, [token, refreshUser]);

  return (
    <div className="w-full max-w-md text-center">
      <LogoMark size={56} className="mx-auto shadow-lg shadow-black/40" />
      <h1 className="mt-6 text-2xl font-semibold tracking-tight">Xác thực email</h1>
      {state === "working" && <p className="mt-4 text-sm text-white/60">Đang xác thực...</p>}
      {state === "ok" && (
        <>
          <p className="mt-4 text-sm text-emerald-300">Email đã được xác thực. Cảm ơn bạn!</p>
          <Link
            href={user ? "/chat" : "/login"}
            className="brand-gradient mt-8 inline-block rounded-full px-6 py-3 text-sm font-semibold text-white"
          >
            {user ? "Vào ứng dụng" : "Đăng nhập"}
          </Link>
        </>
      )}
      {state === "error" && (
        <>
          <p className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            {message}
          </p>
          <p className="mt-6 text-sm text-white/50">
            Đăng nhập rồi bấm “Gửi lại email xác thực” ở đầu trang để nhận link mới.
          </p>
        </>
      )}
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <AuthBackdrop>
      <Suspense fallback={<p className="text-sm text-white/60">Đang tải...</p>}>
        <VerifyEmailInner />
      </Suspense>
    </AuthBackdrop>
  );
}
