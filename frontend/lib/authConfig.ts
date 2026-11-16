"use client";

import { useEffect, useState } from "react";
import api from "./api";

export type AuthConfig = {
  google_client_id: string | null;
  email_verification_required: boolean;
  password_reset_enabled: boolean;
};

const FALLBACK: AuthConfig = {
  google_client_id: null,
  email_verification_required: false,
  password_reset_enabled: false,
};

let cached: Promise<AuthConfig> | null = null;

/** Cấu hình đăng nhập do backend công bố (nút Google, bắt buộc xác thực email...). */
export function fetchAuthConfig(): Promise<AuthConfig> {
  if (!cached) {
    cached = api
      .get<AuthConfig>("/auth/config", { timeout: 8000 })
      .then((res) => ({ ...FALLBACK, ...res.data }))
      .catch(() => {
        cached = null; // thử lại lần sau (backend free tier có thể đang khởi động)
        return FALLBACK;
      });
  }
  return cached;
}

export function useAuthConfig(): AuthConfig | null {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  useEffect(() => {
    let alive = true;
    void fetchAuthConfig().then((c) => alive && setConfig(c));
    return () => {
      alive = false;
    };
  }, []);
  return config;
}
