import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import * as api from "./api";
import { unregisterPush } from "./push";
import { PRIVACY_VERSION } from "./privacy";

type AuthContextValue = {
  user: api.User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  loginWithGoogle: (idToken: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Bước 7 (P1): đồng ý phiên bản chính sách hiện hành do máy chủ báo. */
  acceptPrivacy: () => Promise<void>;
  /** Xoá tài khoản trên máy chủ rồi đăng xuất khỏi máy này. */
  deleteAccount: (confirm: string, password: string | null) => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<api.User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    let changed = false;
    const unsubscribe = api.subscribeAuth((nextUser) => {
      changed = true;
      if (alive) { setUser(nextUser); setLoading(false); }
    });
    void (async () => {
      try {
        const cached = await api.loadUser();
        const token = await api.getToken();
        if (alive && !changed) setUser(cached && token ? cached : null);
        if (cached && token) {
          // Hồ sơ mới nhất từ máy chủ (ví dụ chính sách vừa đổi phiên bản). Mất mạng thì giữ bản đã lưu.
          void api.getMe().then((fresh) => api.updateStoredUser(fresh)).catch(() => undefined);
        }
      } catch {
        if (alive && !changed) setUser(null);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; unsubscribe(); };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await api.login(email, password);
    await api.saveAuth(res.access_token, res.user);
    setUser(res.user);
  }, []);

  const register = useCallback(async (email: string, password: string) => {
    const res = await api.register(email, password, PRIVACY_VERSION);
    await api.saveAuth(res.access_token, res.user);
    setUser(res.user);
  }, []);

  const loginWithGoogle = useCallback(async (idToken: string) => {
    const res = await api.loginWithGoogle(idToken);
    await api.saveAuth(res.access_token, res.user);
    setUser(res.user);
  }, []);

  const logout = useCallback(async () => {
    // M15: máy này thôi nhận thông báo của người vừa đăng xuất (cần token còn hiệu lực).
    await unregisterPush().catch(() => undefined);
    await api.clearAuth();
    setUser(null);
  }, []);

  const acceptPrivacy = useCallback(async () => {
    const version = user?.privacy_version_current;
    if (!version) return;
    await api.updateStoredUser(await api.acceptPrivacy(version));
  }, [user]);

  const deleteAccount = useCallback(async (confirm: string, password: string | null) => {
    await api.deleteMyAccount(confirm, password);
    // Máy chủ đã xoá máy nhận thông báo và thu hồi phiên; chỉ cần dọn trên máy này.
    await unregisterPush().catch(() => undefined);
    await api.clearAuth();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, register, loginWithGoogle, logout, acceptPrivacy, deleteAccount }),
    [user, loading, login, register, loginWithGoogle, logout, acceptPrivacy, deleteAccount]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth phải dùng trong AuthProvider");
  return ctx;
}
