"use client";

import { useState } from "react";
import Link from "next/link";
import api, { getApiErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";

type Team = { id: number; name: string; cases?: number };
type Preview = {
  teams_deleted: Team[];
  teams_blocked: Team[];
  teams_left: Team[];
  requires_password: boolean;
  confirm_word: string;
};

/** Cài đặt → Dữ liệu cá nhân: tải dữ liệu của tôi, xoá tài khoản (quyền của chủ thể dữ liệu). */
export default function PersonalDataSection() {
  const { logout } = useAuth();
  const [downloading, setDownloading] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setDownloading(true);
    setError(null);
    try {
      const res = await api.get("/auth/me/export", { responseType: "blob" });
      const url = URL.createObjectURL(res.data as Blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `fieldops-du-lieu-cua-toi-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(getApiErrorMessage(err));
    } finally {
      setDownloading(false);
    }
  }

  async function openDelete() {
    setError(null);
    try {
      setPreview((await api.get<Preview>("/auth/me/delete-preview")).data);
    } catch (err) {
      setError(getApiErrorMessage(err));
    }
  }

  async function confirmDelete() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/me/delete", { confirm, password: preview.requires_password ? password : null });
      // Tài khoản đã xoá ở máy chủ: dọn hồ sơ, bộ nhớ offline trên máy rồi về màn đăng nhập.
      logout();
    } catch (err) {
      setError(getApiErrorMessage(err));
      setBusy(false);
    }
  }

  const canDelete =
    preview !== null &&
    confirm.trim().toUpperCase() === preview.confirm_word &&
    (!preview.requires_password || password.length > 0);

  return (
    <section className="space-y-3">
      <h2 className="font-medium">Dữ liệu cá nhân</h2>
      <p className="text-xs text-muted-foreground">
        Xem{" "}
        <Link href="/privacy" className="underline">
          Chính sách quyền riêng tư
        </Link>
        . Thông tin khách hàng trong ca thuộc về đội và do trưởng đội quản lý.
      </p>
      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" onClick={download} disabled={downloading}>
          {downloading ? "Đang chuẩn bị..." : "Tải dữ liệu của tôi"}
        </Button>
        {!preview && (
          <Button size="sm" variant="destructive" onClick={openDelete}>
            Xoá tài khoản
          </Button>
        )}
      </div>

      {preview && (
        <div className="space-y-3 rounded-lg border border-destructive/40 p-4 text-sm">
          <p className="font-medium text-destructive">Xoá tài khoản không thể hoàn tác.</p>
          <ul className="list-disc space-y-1 pl-5 text-slate-700">
            <li>
              Email, hội thoại với trợ lý, tài liệu cá nhân và thông báo trên điện thoại sẽ bị xoá; bạn bị đăng xuất
              khỏi mọi thiết bị.
            </li>
            {preview.teams_deleted.map((t) => (
              <li key={t.id}>
                Đội <b>{t.name}</b> chỉ có mình bạn: đội và <b>{t.cases ?? 0} ca</b> (kèm ảnh, chữ ký) sẽ bị xoá hẳn.
              </li>
            ))}
            {preview.teams_left.map((t) => (
              <li key={t.id}>
                Bạn rời đội <b>{t.name}</b>; ca của đội vẫn còn, ca đang giao cho bạn sẽ chưa có người phụ trách.
              </li>
            ))}
          </ul>
          {preview.teams_blocked.length > 0 ? (
            <p className="rounded-md bg-amber-50 px-3 py-2 text-amber-900">
              Bạn là trưởng đội duy nhất của {preview.teams_blocked.map((t) => t.name).join(", ")}. Hãy chuyển quyền
              trưởng đội cho người khác trước khi xoá tài khoản.
            </p>
          ) : (
            <>
              {preview.requires_password && (
                <label className="block">
                  <span className="text-xs text-muted-foreground">Mật khẩu hiện tại</span>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="current-password"
                    className="mt-1 w-full rounded-md border px-3 py-2"
                  />
                </label>
              )}
              <label className="block">
                <span className="text-xs text-muted-foreground">
                  Gõ <b>{preview.confirm_word}</b> để xác nhận
                </span>
                <input
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  className="mt-1 w-full rounded-md border px-3 py-2"
                />
              </label>
            </>
          )}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="ghost" onClick={() => setPreview(null)} disabled={busy}>
              Huỷ
            </Button>
            {preview.teams_blocked.length === 0 && (
              <Button size="sm" variant="destructive" onClick={confirmDelete} disabled={busy || !canDelete}>
                {busy ? "Đang xoá..." : "Xoá vĩnh viễn tài khoản"}
              </Button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
