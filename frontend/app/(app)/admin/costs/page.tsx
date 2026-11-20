"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";
import api, { getApiErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type Breakdown = { cost_usd: number; input_tokens: number; output_tokens: number; requests: number };
type DayPoint = Breakdown & { date: string };
type InfraItem = { id: number; month: string; provider: string; item: string; amount_usd: number; note: string | null };
type CostSummary = {
  billing_tier: "free" | "paid";
  pricing_checked_at: string;
  totals: {
    llm_usd: number;
    input_tokens: number;
    output_tokens: number;
    requests: number;
    answer_requests: number;
    cost_per_answer_usd: number;
    estimated_events: number;
    error_events: number;
  };
  by_day: DayPoint[];
  by_feature: (Breakdown & { feature: string })[];
  by_model: (Breakdown & { model: string })[];
  top_users: (Breakdown & { user_id: number; email: string })[];
  month: {
    month: string;
    llm_to_date_usd: number;
    llm_projected_usd: number;
    infra_usd: number;
    total_projected_usd: number;
    infra_items: InfraItem[];
  };
  budget: { daily_limit_usd: number; user_daily_limit_usd: number; spent_today_usd: number };
};

const FEATURE_LABELS: Record<string, string> = {
  chat: "Trợ lý chat",
  field_assistant: "Trợ lý ca hiện trường",
  public_api: "Public API",
  embed_chat: "Widget nhúng",
  embed_ingest: "Lập chỉ mục tài liệu",
  embed_query: "Embedding câu hỏi",
  judge: "Chấm điểm tự động",
  suggest: "Gợi ý câu hỏi",
  ocr: "OCR ảnh",
  rerank: "Rerank (Cohere)",
  eval: "Bộ đánh giá",
};

function usd(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(5)}`;
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function tokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-xs">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

/** Cột bo tròn 4px ở đầu dữ liệu, chân cột vuông trên trục. */
function roundedTop(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w / 2, h);
  const base = y + h;
  return `M${x},${base} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${base} Z`;
}

/** Cột chi phí theo ngày — 1 chuỗi, 1 màu; hover để xem số. */
function DailyChart({ days }: { days: DayPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(...days.map((d) => d.cost_usd), 0);
  const width = 720;
  const height = 180;
  const pad = { top: 12, right: 8, bottom: 24, left: 56 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const slot = innerW / Math.max(days.length, 1);
  const barW = Math.max(2, Math.min(18, slot - 2));
  const ticks = max > 0 ? [0, max / 2, max] : [0];
  const active = hover !== null ? days[hover] : null;

  return (
    <div className="relative">
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full min-w-[560px]"
          role="img"
          aria-label="Chi phí AI theo ngày"
        >
          {ticks.map((t) => {
            const y = pad.top + innerH - (max ? (t / max) * innerH : 0);
            return (
              <g key={t}>
                <line x1={pad.left} x2={width - pad.right} y1={y} y2={y} stroke="#e2e8f0" strokeWidth={1} />
                <text x={pad.left - 6} y={y + 4} textAnchor="end" fontSize={10} fill="#64748b">
                  {usd(t)}
                </text>
              </g>
            );
          })}
          {days.map((d, i) => {
            const h = max ? (d.cost_usd / max) * innerH : 0;
            const x = pad.left + i * slot + (slot - barW) / 2;
            const y = pad.top + innerH - h;
            return (
              <g key={d.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                {/* vùng hover rộng hơn cột */}
                <rect x={pad.left + i * slot} y={pad.top} width={slot} height={innerH} fill="transparent" />
                {h > 0 && (
                  <path d={roundedTop(x, y, barW, h)} fill={hover === i ? "hsl(243 75% 48%)" : "hsl(243 75% 59%)"} />
                )}
                {(i === 0 || i === days.length - 1 || i === Math.floor(days.length / 2)) && (
                  <text
                    x={pad.left + i * slot + slot / 2}
                    y={height - 6}
                    textAnchor="middle"
                    fontSize={10}
                    fill="#64748b"
                  >
                    {d.date.slice(5)}
                  </text>
                )}
              </g>
            );
          })}
          <line x1={pad.left} x2={width - pad.right} y1={pad.top + innerH} y2={pad.top + innerH} stroke="#cbd5e1" />
        </svg>
      </div>
      {active && (
        <div className="pointer-events-none absolute right-2 top-0 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-md">
          <p className="font-medium text-slate-900">{active.date}</p>
          <p className="text-slate-600">
            Chi phí: <span className="tabular-nums text-slate-900">{usd(active.cost_usd)}</span>
          </p>
          <p className="text-slate-600">
            Lượt gọi: <span className="tabular-nums text-slate-900">{active.requests}</span>
          </p>
          <p className="text-slate-600">
            Token:{" "}
            <span className="tabular-nums text-slate-900">
              {tokens(active.input_tokens)} vào / {tokens(active.output_tokens)} ra
            </span>
          </p>
        </div>
      )}
    </div>
  );
}

function BreakdownTable<T extends Breakdown>({
  rows,
  label,
  name,
}: {
  rows: T[];
  label: string;
  name: (row: T) => string;
}) {
  const total = rows.reduce((s, r) => s + r.cost_usd, 0);
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-xs">
      <h3 className="mb-3 text-sm font-medium text-slate-900">{label}</h3>
      {rows.length === 0 ? (
        <p className="text-sm text-slate-500">Chưa có dữ liệu.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="pb-2 font-normal">Hạng mục</th>
              <th className="pb-2 font-normal text-right">Lượt</th>
              <th className="pb-2 font-normal text-right">Token vào/ra</th>
              <th className="pb-2 font-normal text-right">Chi phí</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const share = total ? r.cost_usd / total : 0;
              return (
                <tr key={name(r)} className="border-t border-slate-100">
                  <td className="py-2 pr-2">
                    <div className="text-slate-800">{name(r)}</div>
                    <div className="mt-1 h-1.5 rounded-full bg-slate-100">
                      <div
                        className="h-1.5 rounded-full bg-primary"
                        style={{ width: `${Math.max(share * 100, share ? 2 : 0)}%` }}
                      />
                    </div>
                  </td>
                  <td className="py-2 text-right tabular-nums text-slate-600">{r.requests}</td>
                  <td className="py-2 text-right tabular-nums text-slate-600">
                    {tokens(r.input_tokens)} / {tokens(r.output_tokens)}
                  </td>
                  <td className="py-2 text-right tabular-nums text-slate-900">{usd(r.cost_usd)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function InfraForm({ month, onSaved }: { month: string; onSaved: () => void }) {
  const [provider, setProvider] = useState("");
  const [item, setItem] = useState("");
  const [amount, setAmount] = useState("0");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post("/admin/costs/infra", { month, provider, item, amount_usd: Number(amount) || 0 });
      setProvider("");
      setItem("");
      setAmount("0");
      onSaved();
    } catch (err) {
      setError(getApiErrorMessage(err));
    }
  }

  const input = "rounded-md border border-slate-200 px-2.5 py-1.5 text-sm";
  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
      <input
        className={input}
        placeholder="Nhà cung cấp (VD Oracle Cloud)"
        value={provider}
        onChange={(e) => setProvider(e.target.value)}
        required
      />
      <input
        className={input}
        placeholder="Khoản mục (VD VM A1 2 OCPU)"
        value={item}
        onChange={(e) => setItem(e.target.value)}
        required
      />
      <input
        className={`${input} w-28`}
        type="number"
        step="0.01"
        min="0"
        value={amount}
        onChange={(e) => setAmount(e.target.value)}
        aria-label="Số tiền USD"
      />
      <button type="submit" className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-white">
        Thêm
      </button>
      {error && <p className="w-full text-xs text-red-600">{error}</p>}
    </form>
  );
}

export default function CostsPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [days, setDays] = useState(30);
  const [data, setData] = useState<CostSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showTable, setShowTable] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await api.get<CostSummary>(`/admin/costs/summary?days=${days}`);
      setData(res.data);
    } catch (err) {
      setError(getApiErrorMessage(err));
    }
  }, [days]);

  useEffect(() => {
    if (!loading && user && user.role !== "admin") router.replace("/chat");
  }, [loading, user, router]);

  useEffect(() => {
    if (user?.role === "admin") void load();
  }, [user, load]);

  const budgetPct = useMemo(() => {
    if (!data?.budget.daily_limit_usd) return null;
    return Math.min(100, (data.budget.spent_today_usd / data.budget.daily_limit_usd) * 100);
  }, [data]);

  if (loading || !user || user.role !== "admin") {
    return <div className="p-6 text-sm text-muted-foreground">Đang tải...</div>;
  }

  async function removeInfra(id: number) {
    await api.delete(`/admin/costs/infra/${id}`);
    await load();
  }

  return (
    <div className="h-full space-y-6 overflow-y-auto p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-xl font-semibold">Chi phí AI & hạ tầng</h1>
          {data && (
            <p className="mt-1 text-xs text-slate-500">
              {data.billing_tier === "free"
                ? "Đang dùng Gemini free tier — số tiền là giá trị quy đổi theo giá paid tier, chưa thực trả."
                : "Gemini paid tier — số tiền là chi phí thực theo giá niêm yết."}{" "}
              Bảng giá cập nhật {data.pricing_checked_at}.
            </p>
          )}
        </div>
        <div className="ml-auto flex gap-1 rounded-lg border border-slate-200 p-0.5 text-sm">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => setDays(d)}
              className={`rounded-md px-3 py-1 ${days === d ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}
            >
              {d} ngày
            </button>
          ))}
        </div>
      </div>

      {error && <p className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
      {!data && !error && <p className="text-sm text-slate-500">Đang tải số liệu...</p>}

      {data && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label={`Chi phí AI ${days} ngày`}
              value={usd(data.totals.llm_usd)}
              hint={`${data.totals.requests} lượt gọi model`}
            />
            <Stat
              label="Chi phí / câu trả lời"
              value={usd(data.totals.cost_per_answer_usd)}
              hint={`${data.totals.answer_requests} câu trả lời`}
            />
            <Stat
              label="Token đã dùng"
              value={tokens(data.totals.input_tokens + data.totals.output_tokens)}
              hint={`${tokens(data.totals.input_tokens)} vào · ${tokens(data.totals.output_tokens)} ra`}
            />
            <Stat
              label={`Dự kiến tháng ${data.month.month}`}
              value={usd(data.month.total_projected_usd)}
              hint={`AI ${usd(data.month.llm_projected_usd)} + hạ tầng ${usd(data.month.infra_usd)}`}
            />
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-xs">
            <div className="mb-2 flex items-center">
              <h3 className="text-sm font-medium text-slate-900">Chi phí AI theo ngày (USD)</h3>
              <button
                type="button"
                onClick={() => setShowTable((v) => !v)}
                className="ml-auto text-xs text-slate-500 hover:text-slate-900"
              >
                {showTable ? "Xem biểu đồ" : "Xem bảng số"}
              </button>
            </div>
            {showTable ? (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500">
                    <th className="font-normal">Ngày</th>
                    <th className="text-right font-normal">Lượt</th>
                    <th className="text-right font-normal">Token vào</th>
                    <th className="text-right font-normal">Token ra</th>
                    <th className="text-right font-normal">Chi phí</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_day
                    .filter((d) => d.requests > 0)
                    .map((d) => (
                      <tr key={d.date} className="border-t border-slate-100 tabular-nums">
                        <td className="py-1">{d.date}</td>
                        <td className="text-right">{d.requests}</td>
                        <td className="text-right">{tokens(d.input_tokens)}</td>
                        <td className="text-right">{tokens(d.output_tokens)}</td>
                        <td className="text-right">{usd(d.cost_usd)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            ) : (
              <DailyChart days={data.by_day} />
            )}
            {(data.totals.estimated_events > 0 || data.totals.error_events > 0) && (
              <p className="mt-2 text-xs text-slate-500">
                {data.totals.estimated_events} lượt có token ước lượng (embedding không trả usage) ·{" "}
                {data.totals.error_events} lượt lỗi/ngắt giữa chừng.
              </p>
            )}
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <BreakdownTable
              rows={data.by_feature}
              label="Theo tính năng"
              name={(r) => FEATURE_LABELS[r.feature] ?? r.feature}
            />
            <BreakdownTable rows={data.by_model} label="Theo model" name={(r) => r.model} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <BreakdownTable rows={data.top_users} label="Người dùng tốn nhiều nhất" name={(r) => r.email} />
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-xs">
              <h3 className="text-sm font-medium text-slate-900">Ngân sách AI hôm nay</h3>
              {budgetPct === null ? (
                <p className="mt-2 text-sm text-slate-500">
                  Chưa đặt giới hạn. Đặt <code className="text-xs">LLM_DAILY_BUDGET_USD</code> để tự chặn khi vượt. Đã
                  dùng hôm nay: {usd(data.budget.spent_today_usd)}.
                </p>
              ) : (
                <>
                  <p className="mt-2 text-sm tabular-nums">
                    {usd(data.budget.spent_today_usd)} / {usd(data.budget.daily_limit_usd)}
                  </p>
                  <div className="mt-2 h-2 rounded-full bg-slate-100">
                    <div
                      className={`h-2 rounded-full ${budgetPct >= 90 ? "bg-red-500" : "bg-primary"}`}
                      style={{ width: `${budgetPct}%` }}
                    />
                  </div>
                  {budgetPct >= 90 && (
                    <p className="mt-1 text-xs text-red-600">
                      Sắp chạm giới hạn — yêu cầu mới sẽ bị từ chối khi đạt 100%.
                    </p>
                  )}
                </>
              )}
              {data.budget.user_daily_limit_usd > 0 && (
                <p className="mt-2 text-xs text-slate-500">
                  Giới hạn mỗi người dùng: {usd(data.budget.user_daily_limit_usd)}/ngày.
                </p>
              )}
            </div>
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-xs">
            <h3 className="text-sm font-medium text-slate-900">Chi phí hạ tầng tháng {data.month.month}</h3>
            <p className="mb-3 mt-1 text-xs text-slate-500">
              Nhập tay từng khoản (VPS, tên miền, dịch vụ ngoài). Gói free vẫn nên ghi $0 để biết đang phụ thuộc vào
              đâu.
            </p>
            {data.month.infra_items.length > 0 && (
              <table className="mb-3 w-full text-sm">
                <tbody>
                  {data.month.infra_items.map((r) => (
                    <tr key={r.id} className="border-t border-slate-100">
                      <td className="py-1.5">{r.provider}</td>
                      <td className="py-1.5 text-slate-600">{r.item}</td>
                      <td className="py-1.5 text-right tabular-nums">{usd(r.amount_usd)}</td>
                      <td className="w-8 py-1.5 text-right">
                        <button
                          type="button"
                          onClick={() => removeInfra(r.id)}
                          aria-label="Xoá"
                          className="text-slate-400 hover:text-red-600"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <InfraForm month={data.month.month} onSaved={load} />
          </div>
        </>
      )}
    </div>
  );
}
