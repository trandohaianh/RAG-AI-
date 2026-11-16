import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import * as api from "./api";

type AuthContextValue = {
  user: api.User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
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
    const res = await api.register(email, password);
    await api.saveAuth(res.access_token, res.user);
    setUser(res.user);
  }, []);

  const logout = useCallback(async () => {
    await api.clearAuth();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, register, logout }),
    [user, loading, login, register, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth phải dùng trong AuthProvider");
  return ctx;
}
