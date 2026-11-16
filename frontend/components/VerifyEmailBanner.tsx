"use client";

import { useState } from "react";
import { MailWarning } from "lucide-react";
import api, { getApiErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useAuthConfig } from "@/lib/authConfig";

/** Nhắc xác thực email; chỉ hiện khi máy chủ có gửi email được. */
export default function VerifyEmailBanner() {
  const { user } = useAuth();
  const config = useAuthConfig();
  const [status, setStatus] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  if (!user || user.email_verified !== false || !config?.password_reset_enabled) return null;

  async function resend() {
    setSending(true);
    try {
      const res = await api.post<{ message: string }>("/auth/resend-verification");
      setStatus(res.data.message);
    } catch (err) {
      setStatus(getApiErrorMessage(err));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-[13px] text-amber-900">
      <MailWarning className="h-4 w-4 shrink-0" />
      <span>
        {config.email_verification_required
          ? "Bạn cần xác thực email trước khi dùng trợ lý và tài liệu."
          : "Email của bạn chưa được xác thực."}{" "}
        Kiểm tra hộp thư {user.email}.
      </span>
      <button
        type="button"
        onClick={resend}
        disabled={sending}
        className="ml-auto rounded-md border border-amber-300 bg-white px-2.5 py-1 font-medium hover:bg-amber-100 disabled:opacity-50"
      >
        {sending ? "Đang gửi..." : "Gửi lại email xác thực"}
      </button>
      {status && <span className="w-full text-xs text-amber-800">{status}</span>}
    </div>
  );
}
