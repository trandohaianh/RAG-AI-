"use client";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import { CalendarDays, LoaderCircle, MapPinned, Route as RouteIcon, Save, Wand2 } from "lucide-react";
import api, { getApiErrorMessage, type FieldCase, type FieldMember, type FieldWorkspace, type SchedulePlan } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ROUTE_COLORS, type MapRoute } from "@/components/field/RouteMap";
import { PRIORITY_LABEL } from "@/components/field/CaseDispatchEditor";

const RouteMap = dynamic(() => import("@/components/field/RouteMap"), { ssr: false });

function tomorrow() {
  const d = new Date(Date.now() + 86400000);
  return d.toISOString().slice(0, 10);
}
const hhmm = (iso: string) => new Date(iso).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });

type SavedRoute = {
  technician_id: number;
  technician: string | null;
  stops: { case_id: number; title: string; address: string | null; priority: FieldCase["priority"]; latitude: number | null;
           longitude: number | null; scheduled_start: string }[];
};
type DisplayRoute = {
  technician_id: number;
  technician: string | null;
  summary: string | null;
  stops: { case_id: number; title: string; address: string | null; priority: FieldCase["priority"];
           lat: number | null; lng: number | null; time: string }[];
};

export default function SchedulePage() {
  const { user } = useAuth();
  const [workspace, setWorkspace] = useState<FieldWorkspace | null>(null);
  const [members, setMembers] = useState<FieldMember[]>([]);
  const [cases, setCases] = useState<FieldCase[]>([]);
  const [day, setDay] = useState(tomorrow());
  const [plan, setPlan] = useState<SchedulePlan | null>(null);
  const [saved, setSaved] = useState<SavedRoute[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [baseDraft, setBaseDraft] = useState<Record<number, { coords: string; start: string; end: string }>>({});
  const isOwner = workspace?.role === "owner";

  const load = useCallback(async () => {
    try {
      const all = (await api.get<FieldWorkspace[]>("/field/workspaces")).data;
      const savedId = Number(localStorage.getItem("field_workspace_id"));
      const active = all.find((w) => w.id === savedId) ?? all[0];
      if (!active) return;
      setWorkspace(active);
      const [m, c] = await Promise.all([
        api.get<FieldMember[]>(`/field/workspaces/${active.id}/members`),
        api.get<FieldCase[]>(`/field/cases?workspace_id=${active.id}`),
      ]);
      setMembers(m.data);
      setCases(c.data);
      setBaseDraft(Object.fromEntries(m.data.map((x) => [x.user_id, {
        coords: x.base_latitude != null ? `${x.base_latitude}, ${x.base_longitude}` : "",
        start: x.shift_start ?? "08:00", end: x.shift_end ?? "17:00" }])));
    } catch (err) {
      setMessage(getApiErrorMessage(err));
    }
  }, []);

  const loadSaved = useCallback(async () => {
    if (!workspace) return;
    try {
      const res = await api.get(`/field/workspaces/${workspace.id}/schedule`, { params: { date: day } });
      setSaved(res.data.routes);
    } catch {
      setSaved(null);
    }
  }, [workspace, day]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void loadSaved(); }, [loadSaved]);

  async function saveBase(userId: number) {
    const draft = baseDraft[userId];
    const parts = draft.coords.split(",").map((p) => Number(p.trim()));
    if (parts.length !== 2 || parts.some(Number.isNaN)) { setMessage("Toạ độ dạng: 21.0285, 105.8542"); return; }
    try {
      await api.patch(`/field/workspaces/${workspace!.id}/members/${userId}/dispatch`, {
        base_latitude: parts[0], base_longitude: parts[1], shift_start: draft.start, shift_end: draft.end });
      setMessage("Đã lưu điểm xuất phát");
      await load();
    } catch (err) { setMessage(getApiErrorMessage(err)); }
  }

  async function makePlan() {
    if (!workspace) return;
    setBusy(true); setMessage("");
    try {
      setPlan((await api.post<SchedulePlan>(`/field/workspaces/${workspace.id}/schedule/plan`, { date: day })).data);
    } catch (err) { setMessage(getApiErrorMessage(err)); } finally { setBusy(false); }
  }

  async function applyPlan() {
    if (!workspace || !plan) return;
    setBusy(true);
    try {
      const routes = plan.routes.map((r) => ({ technician_id: r.technician_id, case_ids: r.stops.map((s) => s.case_id) }));
      const res = await api.post(`/field/workspaces/${workspace.id}/schedule/apply`, { date: day, routes });
      setMessage(`Đã lưu lịch cho ${res.data.scheduled.length} ca — kỹ thuật viên thấy trên trang Ca hiện trường`);
      setPlan(null);
      await Promise.all([load(), loadSaved()]);
    } catch (err) { setMessage(getApiErrorMessage(err)); } finally { setBusy(false); }
  }

  const baseOf = useCallback((id: number): [number, number] | null => {
    const m = members.find((x) => x.user_id === id);
    return m?.base_latitude != null && m.base_longitude != null ? [m.base_latitude, m.base_longitude] : null;
  }, [members]);

  const display: DisplayRoute[] = useMemo(() => plan
    ? plan.routes.map((r) => ({ technician_id: r.technician_id, technician: r.technician,
        summary: `${r.distance_km} km · ${r.travel_minutes} phút đi`,
        stops: r.stops.map((s) => ({ case_id: s.case_id, title: s.title, address: s.address, priority: s.priority,
          lat: s.latitude, lng: s.longitude, time: `${hhmm(s.start)}–${hhmm(s.end)}` })) }))
    : (saved ?? []).map((r) => ({ technician_id: r.technician_id, technician: r.technician, summary: null,
        stops: r.stops.map((s) => ({ case_id: s.case_id, title: s.title, address: s.address, priority: s.priority,
          lat: s.latitude, lng: s.longitude, time: hhmm(s.scheduled_start) })) })), [plan, saved]);

  const mapRoutes: MapRoute[] = useMemo(() => display.map((r, i) => ({
    color: ROUTE_COLORS[i % ROUTE_COLORS.length], label: r.technician ?? `KTV ${r.technician_id}`, base: baseOf(r.technician_id),
    stops: r.stops.filter((s) => s.lat != null && s.lng != null).map((s) => ({ lat: s.lat!, lng: s.lng!, label: s.title })),
  })), [display, baseOf]);

  const unplaced = useMemo(() => (plan?.unassigned ?? [])
    .map((u) => cases.find((c) => c.id === u.case_id))
    .filter((c): c is FieldCase => !!c && c.latitude != null)
    .map((c) => ({ lat: c.latitude!, lng: c.longitude!, label: c.title })), [plan, cases]);

  const withoutLocation = cases.filter((c) => c.status !== "resolved" && c.latitude == null).length;

  return (
    <div className="mx-auto max-w-7xl space-y-5 p-4 md:p-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold text-slate-900"><RouteIcon className="h-5 w-5 text-indigo-600" /> Điều phối tuyến</h1>
          <p className="mt-1 text-sm text-slate-500">Gán ca cho kỹ thuật viên và xếp thứ tự đi trong ngày, tôn trọng khung giờ hẹn khách và ca làm.</p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm">
            <CalendarDays className="h-4 w-4 text-slate-500" />
            <input type="date" value={day} onChange={(e) => { setDay(e.target.value); setPlan(null); }} className="outline-hidden" />
          </label>
          {isOwner && (
            <button type="button" onClick={() => void makePlan()} disabled={busy}
              className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-60">
              {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />} Lập lịch đề xuất
            </button>
          )}
        </div>
      </header>

      {message && <p className="rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-700">{message}</p>}
      {withoutLocation > 0 && (
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">{withoutLocation} ca đang mở chưa có toạ độ — điền ở mục “Điều phối” trong từng ca để được xếp lịch.</p>
      )}

      <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
        <section className="space-y-3">
          <RouteMap routes={mapRoutes} unplaced={unplaced} />
          {plan && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-100 bg-indigo-50 p-3 text-sm text-indigo-950">
              <span>Đề xuất: {plan.routes.reduce((n, r) => n + r.stops.length, 0)} ca · {plan.distance_km} km · {plan.travel_minutes} phút di chuyển. <span className="text-indigo-900/60">{plan.estimate_note}</span></span>
              <button type="button" onClick={() => void applyPlan()} disabled={busy || !plan.routes.length}
                className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-3 py-1.5 text-white disabled:opacity-60"><Save className="h-4 w-4" /> Lưu lịch</button>
            </div>
          )}
        </section>

        <section className="space-y-3">
          {display.map((route, i) => (
            <div key={route.technician_id} className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <span className="h-3 w-3 rounded-full" style={{ background: ROUTE_COLORS[i % ROUTE_COLORS.length] }} />
                  {route.technician ?? `KTV ${route.technician_id}`}
                </span>
                {route.summary && <span className="text-xs text-slate-500">{route.summary}</span>}
              </div>
              <ol className="mt-2 space-y-1.5">
                {route.stops.map((stop, n) => (
                  <li key={stop.case_id} className="flex gap-2 text-xs">
                    <span className="w-5 shrink-0 font-semibold text-slate-400">{n + 1}.</span>
                    <span className="flex-1">
                      <span className="font-medium text-slate-800">{stop.title}</span>
                      {stop.address && <span className="block text-slate-500">{stop.address}</span>}
                    </span>
                    <span className="shrink-0 text-right text-slate-600">
                      {stop.time}
                      <span className="block text-[10px] text-slate-400">{PRIORITY_LABEL[stop.priority] ?? stop.priority}</span>
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          ))}
          {!plan && !saved?.length && <p className="rounded-xl border border-dashed border-slate-300 p-4 text-sm text-slate-500">Chưa có lịch cho ngày này.</p>}
          {plan?.unassigned.length ? (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs text-amber-900">
              <p className="font-semibold">Chưa xếp được ({plan.unassigned.length})</p>
              <ul className="mt-1 list-disc pl-4">{plan.unassigned.map((u) => <li key={u.case_id}>#{u.case_id} {u.title}: {u.reason}</li>)}</ul>
            </div>
          ) : null}
        </section>
      </div>

      <section className="rounded-xl border border-slate-200 bg-white p-4">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900"><MapPinned className="h-4 w-4 text-indigo-600" /> Điểm xuất phát và ca làm</h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-slate-500"><tr><th className="py-1">Kỹ thuật viên</th><th>Toạ độ xuất phát</th><th>Bắt đầu</th><th>Kết thúc</th><th /></tr></thead>
            <tbody>
              {members.map((m) => {
                const d = baseDraft[m.user_id] ?? { coords: "", start: "08:00", end: "17:00" };
                const editable = isOwner || m.user_id === user?.id;
                const set = (patch: Partial<typeof d>) => setBaseDraft((all) => ({ ...all, [m.user_id]: { ...d, ...patch } }));
                return (
                  <tr key={m.user_id} className="border-t border-slate-100">
                    <td className="py-2 pr-2">{m.email}</td>
                    <td className="pr-2"><input disabled={!editable} value={d.coords} onChange={(e) => set({ coords: e.target.value })} placeholder="21.0285, 105.8542" className="w-44 rounded-md border border-slate-200 px-2 py-1" /></td>
                    <td className="pr-2"><input disabled={!editable} type="time" value={d.start} onChange={(e) => set({ start: e.target.value })} className="rounded-md border border-slate-200 px-2 py-1" /></td>
                    <td className="pr-2"><input disabled={!editable} type="time" value={d.end} onChange={(e) => set({ end: e.target.value })} className="rounded-md border border-slate-200 px-2 py-1" /></td>
                    <td>{editable && <button type="button" onClick={() => void saveBase(m.user_id)} className="rounded-md border border-slate-200 px-2 py-1 hover:bg-slate-50">Lưu</button>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
