"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import api, { SESSION_KEY, USER_KEY, currentSession, dropLegacyToken, peekLegacyToken } from "./api";

export type User = {
  id: number;
  email: string;
  role: string;
  subscription_plan?: string;
  auth_provider?: string;
  email_verified?: boolean;
  has_google?: boolean;
  created_at: string;
  /** Phiên bản Chính sách quyền riêng tư đã đồng ý / phiên bản hiện hành (bước 7, P1). */
  privacy_version?: string | null;
  privacy_version_current?: string;
};

type AuthContextValue = {
  user: User | null;
  loading: boolean;
  /** Gọi sau khi API đăng nhập thành công: phiên đã nằm trong cookie httpOnly. */
  login: (user: User) => void;
  logout: () => void;
  refreshUser: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function newSessionEpoch(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Lưu hồ sơ người dùng (không phải bí mật) và đánh dấu phiên mới cho các tab khác. */
export function saveAuth(user: User) {
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  localStorage.setItem(SESSION_KEY, newSessionEpoch());
}

export function clearAuth() {
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(SESSION_KEY);
}

export function loadUser(): User | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

/** Phiên cũ lưu token trong localStorage: đổi sang cookie httpOnly một lần rồi xoá token.
 * Dùng chung một promise: AuthProvider có thể mount lại (Providers bọc thêm ThemeProvider sau
 * khi mount) và lượt chạy thứ hai không được kết luận "chưa đăng nhập" khi lượt đầu chưa xong. */
let legacyMigration: Promise<void> | null = null;

/** Chỉ xoá token cũ khi server đã đổi xong sang cookie, hoặc khi server nói token hỏng (401).
 * Trước đây token bị xoá TRƯỚC khi gọi và mọi lỗi (kể cả mất mạng) đều đăng xuất: kỹ thuật
 * viên mở PWA vừa cập nhật lúc không có sóng mất phiên và không vào được hàng đợi offline. */
export function migrateLegacyToken(): Promise<void> {
  if (!legacyMigration) {
    legacyMigration = (async () => {
      const token = peekLegacyToken();
      if (!token) return;
      try {
        const res = await api.post<User>("/auth/session", {}, { headers: { Authorization: `Bearer ${token}` } });
        dropLegacyToken();
        saveAuth(res.data);
      } catch (err) {
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status === 401 || status === 403) {
          dropLegacyToken();
          clearAuth();
        } else {
          legacyMigration = null; // mất mạng / server lỗi: giữ token, thử lại lần sau
        }
      }
    })();
  }
  return legacyMigration;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshUser = useCallback(async () => {
    await migrateLegacyToken();
    const epoch = currentSession();
    if (!epoch) {
      // Token cũ chưa đổi được vì đang mất mạng: tạm dùng hồ sơ đã lưu để vẫn mở được outbox.
      const pending = peekLegacyToken() ? loadUser() : null;
      if (pending) {
        setUser(pending);
        setLoading(false);
        return;
      }
      // Trình duyệt này chưa đăng nhập: không hỏi server (tránh 401 cho khách vãng lai).
      setUser(null);
      setLoading(false);
      return;
    }
    const cached = loadUser();
    if (cached) {
      setUser(cached);
      setLoading(false);
    }
    try {
      // Cookie httpOnly là nguồn sự thật: hỏi server phiên hiện tại là ai.
      const res = await api.get<User>("/auth/me", { timeout: 8000 });
      if (currentSession() !== epoch) return;
      setUser(res.data);
      localStorage.setItem(USER_KEY, JSON.stringify(res.data));
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      // Mất mạng: giữ hồ sơ đã lưu để vẫn dùng được outbox offline.
      if ((status === 401 || !cached) && currentSession() === epoch) {
        clearAuth();
        setUser(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshUser();
    const onStorage = (event: StorageEvent) => {
      if (event.key === SESSION_KEY || event.key === USER_KEY || event.key === null) {
        setUser(currentSession() ? loadUser() : null);
        void refreshUser();
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [refreshUser]);

  const login = useCallback((u: User) => {
    saveAuth(u);
    setUser(u);
    setLoading(false);
  }, []);

  const logout = useCallback(() => {
    void import("@/lib/caseCache").then((m) => m.clearSnapshots());
    clearAuth();
    setUser(null);
    // Xoá cookie phiên ở server (JS không tự xoá được cookie httpOnly).
    void api.post("/auth/logout").catch(() => undefined);
    router.push("/login");
  }, [router]);

  const value = useMemo(
    () => ({ user, loading, login, logout, refreshUser }),
    [user, loading, login, logout, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth phải dùng trong AuthProvider");
  }
  return ctx;
}
