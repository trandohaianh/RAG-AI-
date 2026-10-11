"use client";

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import api, { getApiErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

type BillingStatus = {
  plan: string;
  stripe_configured: boolean;
  daily_limit: number;
  queries_today: number;
};

type ApiKeyRow = {
  id: number;
  name: string;
  key_prefix: string;
  created_at: string;
  last_used_at: string | null;
};

type EmbedRow = {
  id: number;
  label: string;
  allowed_origins: string | null;
  active: boolean;
  created_at: string;
};

export default function SettingsPage() {
  const searchParams = useSearchParams();
  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [apiKeys, setApiKeys] = useState<ApiKeyRow[]>([]);
  const [embeds, setEmbeds] = useState<EmbedRow[]>([]);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [newEmbed, setNewEmbed] = useState<{ token: string; snippet: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [b, k, e] = await Promise.all([
        api.get<BillingStatus>("/billing/status"),
        api.get<ApiKeyRow[]>("/api-keys"),
        api.get<EmbedRow[]>("/embed/tokens"),
      ]);
      setBilling(b.data);
      setApiKeys(Array.isArray(k.data) ? k.data : []);
      setEmbeds(Array.isArray(e.data) ? e.data : []);
    } catch (err) {
      setError(getApiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    load();
    if (searchParams.get("billing") === "success") {
      setMsg("Thanh toán thành công — gói Pro sẽ được kích hoạt sau vài giây.");
    }
  }, [load, searchParams]);

  async function checkout() {
    try {
      const res = await api.post<{ url: string }>("/billing/checkout");
      window.location.href = res.data.url;
    } catch (e) {
      alert(getApiErrorMessage(e));
    }
  }

  async function portal() {
    try {
      const res = await api.post<{ url: string }>("/billing/portal");
      window.location.href = res.data.url;
    } catch (e) {
      alert(getApiErrorMessage(e));
    }
  }

  async function createKey() {
    try {
      const res = await api.post<ApiKeyRow & { key: string }>("/api-keys", { name: "API" });
      setNewKey(res.data.key);
      load();
    } catch (e) {
      alert(getApiErrorMessage(e));
    }
  }

  async function createEmbed() {
    const origins = prompt("Website được phép nhúng (cách nhau bởi dấu phẩy, để trống = mọi website):");
    try {
      const res = await api.post<{ token: string; snippet: string }>("/embed/tokens", {
        label: "Website",
        allowed_origins: origins || null,
      });
      setNewEmbed({ token: res.data.token, snippet: res.data.snippet });
      load();
    } catch (e) {
      alert(getApiErrorMessage(e));
    }
  }

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6 space-y-10 max-w-3xl">
      <div>
        <h1 className="text-xl font-semibold">Cài đặt</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Gói sử dụng, khóa API cho hệ thống khác và khung chat nhúng vào website
        </p>
      </div>

      {msg && (
        <div className="rounded-md border border-primary/30 bg-primary/5 text-sm px-4 py-3">{msg}</div>
      )}
      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 text-destructive text-sm px-4 py-3">
          {error}
        </div>
      )}

      <section className="space-y-3">
        <h2 className="font-medium">Gói sử dụng & thanh toán</h2>
        {billing && (
          <div className="rounded-lg border p-4 space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">Gói hiện tại</span>
              <Badge>{billing.plan}</Badge>
            </div>
            <p className="text-sm">
              Hôm nay: {billing.queries_today} / {billing.daily_limit} câu hỏi
            </p>
            {billing.stripe_configured ? (
              <div className="flex flex-wrap gap-2">
                {billing.plan !== "pro" && (
                  <Button onClick={checkout}>Nâng cấp Pro</Button>
                )}
                <Button variant="secondary" onClick={portal}>
                  Quản lý gói đăng ký
                </Button>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                Thanh toán trực tuyến chưa được bật (cần cấu hình Stripe trên máy chủ).
              </p>
            )}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="font-medium">Khóa API</h2>
        <p className="text-xs text-muted-foreground">
          Cho phép hệ thống khác hỏi trợ lý qua API: POST /v1/ask, header X-API-Key
        </p>
        <Button size="sm" onClick={createKey}>
          Tạo khóa API
        </Button>
        {newKey && (
          <div className="rounded-md bg-muted p-3 text-xs break-all">
            <p className="font-medium text-destructive mb-1">Lưu khóa ngay — chỉ hiện một lần:</p>
            {newKey}
          </div>
        )}
        <ul className="border rounded-lg divide-y text-sm">
          {apiKeys.map((k) => (
            <li key={k.id} className="p-3 flex justify-between">
              <span>
                {k.name} — <code>{k.key_prefix}…</code>
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={async () => {
                  await api.delete(`/api-keys/${k.id}`);
                  load();
                }}
              >
                Xóa
              </Button>
            </li>
          ))}
        </ul>
      </section>

      <section className="space-y-3">
        <h2 className="font-medium">Nhúng khung chat vào website</h2>
        <Button size="sm" onClick={createEmbed}>
          Tạo mã nhúng
        </Button>
        {newEmbed && (
          <div className="rounded-md bg-muted p-3 text-xs space-y-2">
            <p className="font-medium">Đoạn mã dán vào website:</p>
            <pre className="whitespace-pre-wrap break-all">{newEmbed.snippet}</pre>
          </div>
        )}
        <ul className="border rounded-lg divide-y text-sm">
          {embeds.map((e) => (
            <li key={e.id} className="p-3 flex justify-between items-center">
              <span>
                {e.label} {e.active ? "" : "(đã tắt)"}
              </span>
              {e.active && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={async () => {
                    await api.delete(`/embed/tokens/${e.id}`);
                    load();
                  }}
                >
                  Thu hồi
                </Button>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
