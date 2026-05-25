"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import api, { getApiErrorMessage } from "@/lib/api";
import {
  StatsCards,
  UsersTable,
  type AdminStats,
  type AdminUser,
} from "@/components/AdminTable";

export default function AdminPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dataLoading, setDataLoading] = useState(false);

  const load = useCallback(async () => {
    setDataLoading(true);
    setError(null);
    try {
      const [s, u] = await Promise.all([
        api.get<AdminStats>("/admin/stats"),
        api.get<AdminUser[]>("/admin/users"),
      ]);
      setStats(s.data);
      setUsers(Array.isArray(u.data) ? u.data : []);
    } catch (e) {
      setStats(null);
      setUsers([]);
      setError(getApiErrorMessage(e));
    } finally {
      setDataLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!loading && user && user.role !== "admin") {
      router.replace("/chat");
    }
  }, [loading, user, router]);

  useEffect(() => {
    if (user?.role === "admin") load();
  }, [user, load]);

  if (loading || !user) {
    return <div className="p-6 text-muted-foreground">Đang tải...</div>;
  }

  if (user.role !== "admin") {
    return null;
  }

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6 space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Bảng điều khiển Admin</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Tài khoản đăng nhập: {user.email} ({user.role})
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 text-destructive text-sm px-4 py-3">
          {error}
          <p className="mt-2 text-xs text-muted-foreground">
            Chỉ user có role <strong>admin</strong> mới xem được trang này. User đầu tiên đăng ký
            sẽ là admin.
          </p>
        </div>
      )}

      {dataLoading && !stats && !error && (
        <p className="text-sm text-muted-foreground">Đang tải thống kê...</p>
      )}

      {stats && <StatsCards stats={stats} />}

      <div>
        <h2 className="font-medium mb-3">Người dùng ({users.length})</h2>
        {users.length === 0 && !dataLoading && !error ? (
          <p className="text-sm text-muted-foreground border rounded-lg p-4">Chưa có người dùng.</p>
        ) : (
          <UsersTable users={users} onRefresh={load} />
        )}
      </div>
    </div>
  );
}
