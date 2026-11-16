"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import api, { getApiErrorMessage } from "@/lib/api";
import { useAuth, type User } from "@/lib/auth";
import { useAuthConfig } from "@/lib/authConfig";

type GoogleCredentialResponse = { credential?: string };
type GoogleAccountsId = {
  initialize: (options: {
    client_id: string;
    callback: (response: GoogleCredentialResponse) => void;
    ux_mode?: "popup" | "redirect";
    auto_select?: boolean;
    cancel_on_tap_outside?: boolean;
  }) => void;
  renderButton: (element: HTMLElement, options: Record<string, unknown>) => void;
};

declare global {
  interface Window {
    google?: { accounts?: { id?: GoogleAccountsId } };
  }
}

const GSI_SRC = "https://accounts.google.com/gsi/client";
let scriptPromise: Promise<void> | null = null;

function loadGoogleScript(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.google?.accounts?.id) return Promise.resolve();
  if (!scriptPromise) {
    scriptPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = GSI_SRC;
      script.async = true;
      script.defer = true;
      script.onload = () => resolve();
      script.onerror = () => {
        scriptPromise = null;
        reject(new Error("Không tải được Google Sign-In"));
      };
      document.head.appendChild(script);
    });
  }
  return scriptPromise;
}

/** Nút "Tiếp tục với Google" (Google Identity Services). Ẩn nếu backend chưa bật. */
export default function GoogleSignInButton({ onError }: { onError?: (message: string) => void }) {
  const config = useAuthConfig();
  const { login } = useAuth();
  const router = useRouter();
  const holder = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const clientId = config?.google_client_id ?? null;

  useEffect(() => {
    if (!clientId || !holder.current) return;
    let cancelled = false;
    loadGoogleScript()
      .then(() => {
        const gid = window.google?.accounts?.id;
        if (cancelled || !gid || !holder.current) return;
        gid.initialize({
          client_id: clientId,
          ux_mode: "popup",
          cancel_on_tap_outside: true,
          callback: async (response) => {
            if (!response.credential) return;
            setBusy(true);
            try {
              const res = await api.post<{ user: User }>("/auth/google", {
                credential: response.credential,
              });
              login(res.data.user);
              router.replace("/chat");
            } catch (err) {
              onError?.(getApiErrorMessage(err));
            } finally {
              setBusy(false);
            }
          },
        });
        holder.current.innerHTML = "";
        gid.renderButton(holder.current, {
          theme: "filled_black",
          size: "large",
          shape: "pill",
          text: "continue_with",
          locale: "vi",
          width: Math.min(holder.current.clientWidth || 360, 400),
        });
        setReady(true);
      })
      .catch(() => {
        // Bị chặn bởi trình chặn quảng cáo/mạng: ẩn nút, vẫn đăng nhập bằng email được.
        console.warn("Google Sign-In không tải được; dùng đăng nhập email.");
        setUnavailable(true);
      });
    return () => {
      cancelled = true;
    };
  }, [clientId, login, router, onError]);

  if (!clientId || unavailable) return null;

  return (
    <div className="space-y-3">
      <div
        ref={holder}
        className={`flex min-h-[44px] justify-center ${busy ? "pointer-events-none opacity-60" : ""}`}
        aria-busy={busy}
      />
      {ready && (
        <div className="flex items-center gap-3 text-xs text-white/40">
          <span className="h-px flex-1 bg-white/10" />
          hoặc dùng email
          <span className="h-px flex-1 bg-white/10" />
        </div>
      )}
    </div>
  );
}
