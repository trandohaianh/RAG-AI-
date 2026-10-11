"use client";

import { Trash2 } from "lucide-react";
import api from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export type AdminUser = {
  id: number;
  email: string;
  role: string;
  created_at: string;
  doc_count: number;
  message_count: number;
};

export type AdminStats = {
  total_users: number;
  total_docs: number;
  total_messages: number;
  queries_today: number;
};

export function StatsCards({ stats }: { stats: AdminStats }) {
  const cards = [
    { label: "Tổng users", value: stats.total_users },
    { label: "Tổng tài liệu", value: stats.total_docs },
    { label: "Tổng tin nhắn", value: stats.total_messages },
    { label: "Truy vấn hôm nay", value: stats.queries_today },
  ];
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map((c) => (
        <div key={c.label} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm hover:shadow-md transition-shadow">
          <p className="text-sm text-muted-foreground">{c.label}</p>
          <p className="text-2xl font-bold mt-1">{c.value}</p>
        </div>
      ))}
    </div>
  );
}

export function UsersTable({
  users,
  onRefresh,
}: {
  users: AdminUser[];
  onRefresh: () => void;
}) {
  async function del(id: number, email: string) {
    if (!confirm(`Xóa user ${email} và toàn bộ dữ liệu?`)) return;
    await api.delete(`/admin/users/${id}`);
    onRefresh();
  }

  return (
    <div className="border rounded-lg overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-muted/50">
          <tr>
            <th className="text-left p-3">Email</th>
            <th className="text-left p-3">Role</th>
            <th className="text-left p-3">Docs</th>
            <th className="text-left p-3">Messages</th>
            <th className="text-left p-3">Ngày tạo</th>
            <th className="p-3" />
          </tr>
        </thead>
        <tbody>
          {users.length === 0 && (
            <tr>
              <td colSpan={6} className="p-4 text-center text-muted-foreground">
                Không có dữ liệu người dùng
              </td>
            </tr>
          )}
          {users.map((u) => (
            <tr key={u.id} className="border-t">
              <td className="p-3">{u.email}</td>
              <td className="p-3">
                <Badge variant={u.role === "admin" ? "default" : "secondary"}>{u.role}</Badge>
              </td>
              <td className="p-3">{u.doc_count}</td>
              <td className="p-3">{u.message_count}</td>
              <td className="p-3 text-muted-foreground">
                {u.created_at ? String(u.created_at).slice(0, 10) : "—"}
              </td>
              <td className="p-3 text-right">
                <Button variant="ghost" size="icon" onClick={() => del(u.id, u.email)}>
                  <Trash2 className="h-4 w-4 text-destructive" />
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
