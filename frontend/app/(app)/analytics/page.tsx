"use client";

import { useCallback, useEffect, useState } from "react";
import api, { getApiErrorMessage } from "@/lib/api";
import { StatsCards } from "@/components/AdminTable";

type Overview = {
  conversations: number;
  messages: number;
  assistant_replies: number;
  queries_today: number;
  daily_limit: number;
  subscription_plan: string;
  documents: number;
  total_chunks: number;
};

type DailyPoint = { date: string; queries: number };

export default function AnalyticsPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [daily, setDaily] = useState<DailyPoint[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [o, d] = await Promise.all([
        api.get<Overview>("/analytics/overview"),
        api.get<DailyPoint[]>("/analytics/daily?days=14"),
      ]);
      setOverview(o.data);
      setDaily(Array.isArray(d.data) ? d.data : []);
    } catch (e) {
      setError(getApiErrorMessage(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const maxQ = Math.max(1, ...daily.map((x) => x.queries));

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6 space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Thống kê</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Usage trong app RAG — không phải Grafana (Grafana chỉ dành cho ops/DevOps)
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 text-destructive text-sm px-4 py-3">
          {error}
        </div>
      )}

      {overview && (
        <StatsCards
          stats={{
            total_users: 1,
            total_docs: overview.documents,
            total_messages: overview.messages,
            queries_today: overview.queries_today,
          }}
        />
      )}

      {overview && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="rounded-lg border p-4">
            <p className="text-sm text-muted-foreground">Gói</p>
            <p className="text-lg font-semibold capitalize">{overview.subscription_plan}</p>
          </div>
          <div className="rounded-lg border p-4">
            <p className="text-sm text-muted-foreground">Hạn mức hôm nay</p>
            <p className="text-lg font-semibold">
              {overview.queries_today} / {overview.daily_limit}
            </p>
          </div>
          <div className="rounded-lg border p-4">
            <p className="text-sm text-muted-foreground">Tổng chunks</p>
            <p className="text-lg font-semibold">{overview.total_chunks}</p>
          </div>
        </div>
      )}

      <div>
        <h2 className="font-medium mb-3">Truy vấn 14 ngày gần nhất</h2>
        <div className="rounded-lg border p-4 space-y-2">
          {daily.length === 0 && (
            <p className="text-sm text-muted-foreground">Chưa có dữ liệu truy vấn.</p>
          )}
          {daily.map((d) => (
            <div key={d.date} className="flex items-center gap-3 text-sm">
              <span className="w-24 text-muted-foreground shrink-0">{d.date.slice(5)}</span>
              <div className="flex-1 h-6 bg-muted rounded overflow-hidden">
                <div
                  className="h-full bg-primary rounded"
                  style={{ width: `${(d.queries / maxQ) * 100}%` }}
                />
              </div>
              <span className="w-8 text-right font-medium">{d.queries}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
