"use client";

import { useCallback, useEffect, useState } from "react";
import api, { getApiErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";

type Invite = {
  id: number;
  email: string | null;
  note: string | null;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  status: "active" | "used" | "revoked" | "expired";
};

type CreatedInvite = Invite & { code: string; signup_url: string };

const STATUS_LABEL: Record<Invite["status"], string> = {
  active: "Còn hiệu lực",
  used: "Đã dùng",
  revoked: "Đã thu hồi",
  expired: "Hết hạn",
};

/** Lời mời đăng ký (F1-A): tạo mã một lần, xem trạng thái, thu hồi. */
export function InvitesPanel() {
  const [invites, setInvites] = useState<Invite[]>([]);
  const [email, setEmail] = useState("");
  const [days, setDays] = useState(7);
  const [created, setCreated] = useState<CreatedInvite | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await api.get<Invite[]>("/admin/invites");
      setInvites(Array.isArray(res.data) ? res.data : []);
    } catch (e) {
      setError(getApiErrorMessage(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<CreatedInvite>("/admin/invites", { email: email.trim() || null, days });
      setCreated(res.data);
      setEmail("");
      await load();
    } catch (err) {
      setError(getApiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: number) {
    if (!confirm("Thu hồi lời mời này?")) return;
    try {
      await api.delete(`/admin/invites/${id}`);
      await load();
    } catch (err) {
      setError(getApiErrorMessage(err));
    }
  }

  return (
    <div className="space-y-3">
      <form onSubmit={create} className="flex flex-wrap items-end gap-2">
        <div className="min-w-[220px] flex-1">
          <label className="mb-1 block text-xs text-muted-foreground">Email (để trống = ai có mã cũng dùng được)</label>
          <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="tho@congty.vn" />
        </div>
        <div className="w-28">
          <label className="mb-1 block text-xs text-muted-foreground">Hạn (ngày)</label>
          <Input type="number" min={1} max={30} value={days} onChange={(e) => setDays(Number(e.target.value) || 7)} />
        </div>
        <Button type="submit" disabled={busy}>
          {busy ? "Đang tạo..." : "Tạo lời mời"}
        </Button>
      </form>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {created && (
        <div className="rounded-md border border-amber-400/50 bg-amber-50 p-3 text-sm dark:bg-amber-950/30">
          <p className="font-medium">Sao chép link này ngay — mã chỉ hiện một lần:</p>
          <code className="mt-1 block break-all text-xs">{created.signup_url}</code>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="mt-2"
            onClick={() => void navigator.clipboard?.writeText(created.signup_url)}
          >
            Sao chép
          </Button>
        </div>
      )}

      <div className="border rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3">#</th>
              <th className="text-left p-3">Email</th>
              <th className="text-left p-3">Trạng thái</th>
              <th className="text-left p-3">Hết hạn</th>
              <th className="p-3" />
            </tr>
          </thead>
          <tbody>
            {invites.length === 0 && (
              <tr>
                <td colSpan={5} className="p-4 text-center text-muted-foreground">
                  Chưa có lời mời
                </td>
              </tr>
            )}
            {invites.map((inv) => (
              <tr key={inv.id} className="border-t">
                <td className="p-3">{inv.id}</td>
                <td className="p-3">{inv.email ?? "—"}</td>
                <td className="p-3">
                  <Badge variant={inv.status === "active" ? "default" : "secondary"}>{STATUS_LABEL[inv.status]}</Badge>
                </td>
                <td className="p-3 text-muted-foreground">{String(inv.expires_at).slice(0, 10)}</td>
                <td className="p-3 text-right">
                  {inv.status === "active" && (
                    <Button variant="ghost" size="sm" onClick={() => void revoke(inv.id)}>
                      Thu hồi
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
