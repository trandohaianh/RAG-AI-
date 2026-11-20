"use client";
import { useCallback, useEffect, useState } from "react";
import { Copy, KeyRound, Plug, Plus, RefreshCw, RotateCcw } from "lucide-react";
import api, {
  getApiErrorMessage,
  type FieldIntegration,
  type FieldMember,
  type FieldWorkspace,
  type WebhookDelivery,
} from "@/lib/api";
import { formatDateTime } from "@/lib/utils";

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-amber-50 text-amber-700",
  delivered: "bg-emerald-50 text-emerald-700",
  dead: "bg-rose-50 text-rose-700",
};

function sampleCommand(integration: FieldIntegration, secret: string) {
  const body = `{"event_id":"evt-001","type":"${integration.kind === "nms" ? "alarm.raised" : "ticket.created"}","external_ref":"${integration.kind === "nms" ? "OLT1/PON3" : "TCK-1001"}","data":{"title":"Khách báo mất mạng","description":"Đèn LOS đỏ","address":"12 Láng Hạ, Hà Nội","latitude":21.0156,"longitude":105.8131,"${integration.kind === "nms" ? 'severity":"critical' : 'priority":"high'}"}}`;
  return [
    `BODY='${body}'`,
    `TS=$(date +%s)`,
    `SIG="sha256=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac '${secret}' -hex | sed 's/^.* //')"`,
    `curl -X POST '${integration.inbound_url}' -H 'Content-Type: application/json' \\`,
    `  -H "X-FieldOps-Timestamp: $TS" -H "X-FieldOps-Signature: $SIG" -d "$BODY"`,
  ].join("\n");
}

export default function IntegrationsPage() {
  const [workspace, setWorkspace] = useState<FieldWorkspace | null>(null);
  const [members, setMembers] = useState<FieldMember[]>([]);
  const [items, setItems] = useState<FieldIntegration[]>([]);
  const [revealed, setRevealed] = useState<FieldIntegration | null>(null);
  const [form, setForm] = useState({ name: "", kind: "crm", outbound_url: "", default_assignee: "" });
  const [deliveries, setDeliveries] = useState<Record<number, WebhookDelivery[]>>({});
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const all = (await api.get<FieldWorkspace[]>("/field/workspaces")).data;
      const savedId = Number(localStorage.getItem("field_workspace_id"));
      const active = all.find((w) => w.id === savedId) ?? all[0];
      if (!active) return;
      setWorkspace(active);
      if (active.role !== "owner") return;
      const [list, m] = await Promise.all([
        api.get<FieldIntegration[]>(`/field/workspaces/${active.id}/integrations`),
        api.get<FieldMember[]>(`/field/workspaces/${active.id}/members`),
      ]);
      setItems(list.data);
      setMembers(m.data);
    } catch (err) {
      setMessage(getApiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function create() {
    if (!workspace) return;
    try {
      const res = await api.post<FieldIntegration>(`/field/workspaces/${workspace.id}/integrations`, {
        name: form.name,
        kind: form.kind,
        outbound_url: form.outbound_url || null,
        default_assignee: form.default_assignee ? Number(form.default_assignee) : null,
      });
      setRevealed(res.data);
      setForm({ name: "", kind: "crm", outbound_url: "", default_assignee: "" });
      await load();
    } catch (err) {
      setMessage(getApiErrorMessage(err));
    }
  }

  async function rotate(item: FieldIntegration) {
    if (!confirm("Tạo khoá bí mật mới? Hệ thống CRM/NMS phải cập nhật khoá ngay, khoá cũ hết hiệu lực.")) return;
    try {
      setRevealed((await api.post<FieldIntegration>(`/field/integrations/${item.id}/rotate-secret`)).data);
    } catch (err) {
      setMessage(getApiErrorMessage(err));
    }
  }

  async function patch(item: FieldIntegration, body: Record<string, unknown>) {
    try {
      await api.patch(`/field/integrations/${item.id}`, body);
      await load();
    } catch (err) {
      setMessage(getApiErrorMessage(err));
    }
  }

  async function showDeliveries(item: FieldIntegration) {
    try {
      const res = await api.get<WebhookDelivery[]>(`/field/integrations/${item.id}/deliveries`);
      setDeliveries((d) => ({ ...d, [item.id]: res.data }));
    } catch (err) {
      setMessage(getApiErrorMessage(err));
    }
  }

  async function retry(item: FieldIntegration, delivery: WebhookDelivery) {
    try {
      await api.post(`/field/integrations/${item.id}/deliveries/${delivery.id}/retry`);
      await showDeliveries(item);
    } catch (err) {
      setMessage(getApiErrorMessage(err));
    }
  }

  if (workspace && workspace.role !== "owner") {
    return <div className="p-6 text-sm text-slate-600">Chỉ trưởng đội quản lý kết nối CRM/NMS.</div>;
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-4 md:p-6">
      <header>
        <h1 className="flex items-center gap-2 text-xl font-bold text-slate-900">
          <Plug className="h-5 w-5 text-indigo-600" /> Kết nối CRM / NMS
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Ticket khách hàng (CRM) và cảnh báo mạng (NMS) gửi vào sẽ tự tạo/cập nhật/đóng ca. Khi ca đổi trạng thái,
          người phụ trách hoặc lịch, FieldOps báo ngược lại hệ thống của bạn. Mọi lượt gửi đều ký HMAC-SHA256.
        </p>
      </header>
      {message && <p className="rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-700">{message}</p>}

      {revealed?.secret && (
        <section className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm">
          <p className="flex items-center gap-2 font-semibold text-amber-900">
            <KeyRound className="h-4 w-4" /> Khoá bí mật của “{revealed.name}” — chỉ hiện MỘT lần
          </p>
          <code className="mt-2 block break-all rounded bg-white px-2 py-1">{revealed.secret}</code>
          <p className="mt-3 text-xs text-amber-900">Thử ngay bằng lệnh:</p>
          <pre className="mt-1 overflow-x-auto rounded bg-slate-900 p-3 text-[11px] text-slate-100">
            {sampleCommand(revealed, revealed.secret)}
          </pre>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => void navigator.clipboard.writeText(revealed.secret!)}
              className="inline-flex items-center gap-1 rounded-md border border-amber-300 px-2 py-1 text-xs"
            >
              <Copy className="h-3.5 w-3.5" /> Chép khoá
            </button>
            <button
              type="button"
              onClick={() => setRevealed(null)}
              className="rounded-md border border-amber-300 px-2 py-1 text-xs"
            >
              Tôi đã lưu khoá
            </button>
          </div>
        </section>
      )}

      <section className="rounded-xl border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-slate-900">Thêm kết nối</h2>
        <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="Tên (vd. CRM chăm sóc khách hàng)"
            className="rounded-md border border-slate-200 px-3 py-2"
          />
          <select
            value={form.kind}
            onChange={(e) => setForm({ ...form, kind: e.target.value })}
            className="rounded-md border border-slate-200 px-3 py-2"
          >
            <option value="crm">CRM — ticket khách hàng</option>
            <option value="nms">NMS — cảnh báo mạng</option>
          </select>
          <input
            value={form.outbound_url}
            onChange={(e) => setForm({ ...form, outbound_url: e.target.value })}
            placeholder="URL nhận báo ngược (https://..., tuỳ chọn)"
            className="rounded-md border border-slate-200 px-3 py-2"
          />
          <select
            value={form.default_assignee}
            onChange={(e) => setForm({ ...form, default_assignee: e.target.value })}
            className="rounded-md border border-slate-200 px-3 py-2"
          >
            <option value="">Không giao mặc định</option>
            {members.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                Giao cho {m.email}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          onClick={() => void create()}
          disabled={form.name.trim().length < 2}
          className="mt-3 inline-flex items-center gap-2 rounded-md bg-slate-900 px-3 py-2 text-sm text-white disabled:opacity-50"
        >
          <Plus className="h-4 w-4" /> Tạo kết nối
        </button>
      </section>

      {items.map((item) => (
        <section key={item.id} className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <p className="font-semibold text-slate-900">
                {item.name}{" "}
                <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase">{item.kind}</span>
                {!item.is_active && (
                  <span className="ml-1 rounded bg-rose-50 px-1.5 py-0.5 text-[10px] text-rose-700">đã tắt</span>
                )}
              </p>
              <p className="mt-1 break-all text-xs text-slate-500">
                Nhận sự kiện: <code>{item.inbound_url}</code>
              </p>
              <p className="mt-0.5 text-xs text-slate-500">
                Lần nhận cuối: {item.last_inbound_at ? formatDateTime(item.last_inbound_at) : "chưa có"}
                {" · "}Báo ngược:{" "}
                {Object.entries(item.deliveries)
                  .map(([k, v]) => `${k} ${v}`)
                  .join(", ") || "chưa có"}
              </p>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void rotate(item)}
                className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-2 py-1 text-xs"
              >
                <KeyRound className="h-3.5 w-3.5" /> Đổi khoá
              </button>
              <button
                type="button"
                onClick={() => void patch(item, { is_active: !item.is_active })}
                className="rounded-md border border-slate-200 px-2 py-1 text-xs"
              >
                {item.is_active ? "Tắt" : "Bật"}
              </button>
              <button
                type="button"
                onClick={() => void showDeliveries(item)}
                className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-2 py-1 text-xs"
              >
                <RefreshCw className="h-3.5 w-3.5" /> Lượt báo ngược
              </button>
            </div>
          </div>
          <label className="mt-3 flex items-center gap-2 text-xs">
            URL báo ngược
            <input
              defaultValue={item.outbound_url ?? ""}
              onBlur={(e) =>
                e.target.value !== (item.outbound_url ?? "") &&
                void patch(item, { outbound_url: e.target.value || null })
              }
              placeholder="https://crm.example.com/fieldops-webhook"
              className="flex-1 rounded-md border border-slate-200 px-2 py-1"
            />
          </label>
          {deliveries[item.id] && (
            <table className="mt-3 w-full text-left text-xs">
              <thead className="text-slate-500">
                <tr>
                  <th>Sự kiện</th>
                  <th>Trạng thái</th>
                  <th>Lần thử</th>
                  <th>Lỗi gần nhất</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {deliveries[item.id].map((d) => (
                  <tr key={d.id} className="border-t border-slate-100">
                    <td className="py-1.5">
                      {d.event_type}
                      <span className="block text-[10px] text-slate-400">{formatDateTime(d.created_at)}</span>
                    </td>
                    <td>
                      <span className={`rounded px-1.5 py-0.5 ${STATUS_STYLE[d.status]}`}>{d.status}</span>
                    </td>
                    <td>{d.attempts}</td>
                    <td className="max-w-xs truncate text-slate-500">{d.last_error ?? "—"}</td>
                    <td>
                      {d.status === "dead" && (
                        <button
                          type="button"
                          onClick={() => void retry(item, d)}
                          className="inline-flex items-center gap-1 rounded border border-slate-200 px-1.5 py-0.5"
                        >
                          <RotateCcw className="h-3 w-3" /> Gửi lại
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {!deliveries[item.id].length && (
                  <tr>
                    <td colSpan={5} className="py-2 text-slate-500">
                      Chưa có lượt báo ngược.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </section>
      ))}
    </div>
  );
}
