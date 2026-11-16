"use client";
import { useEffect, useState } from "react";
import { Crosshair, MapPin, Save } from "lucide-react";
import api, { getApiErrorMessage, type FieldCase, type FieldPriority } from "@/lib/api";

export const PRIORITY_LABEL: Record<FieldPriority, string> = {
  urgent: "Khẩn cấp", high: "Cao", normal: "Bình thường", low: "Thấp",
};

function toLocalInput(value: string | null) {
  if (!value) return "";
  const d = new Date(value);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Vị trí, ưu tiên, khung giờ hẹn và thời gian xử lý — đầu vào của bảng điều phối tuyến. */
export default function CaseDispatchEditor({ item, onSaved }: { item: FieldCase; onSaved: () => void }) {
  const [coords, setCoords] = useState("");
  const [priority, setPriority] = useState<FieldPriority>("normal");
  const [windowStart, setWindowStart] = useState("");
  const [windowEnd, setWindowEnd] = useState("");
  const [minutes, setMinutes] = useState(60);
  const [message, setMessage] = useState("");

  useEffect(() => {
    setCoords(item.latitude != null && item.longitude != null ? `${item.latitude}, ${item.longitude}` : "");
    setPriority(item.priority ?? "normal");
    setWindowStart(toLocalInput(item.window_start));
    setWindowEnd(toLocalInput(item.window_end));
    setMinutes(item.est_minutes ?? 60);
    setMessage("");
  }, [item.id, item.latitude, item.longitude, item.priority, item.window_start, item.window_end, item.est_minutes]);

  function here() {
    navigator.geolocation?.getCurrentPosition(
      (pos) => setCoords(`${pos.coords.latitude.toFixed(6)}, ${pos.coords.longitude.toFixed(6)}`),
      () => setMessage("Không lấy được vị trí của thiết bị"),
    );
  }

  async function save() {
    setMessage("");
    const body: Record<string, unknown> = { priority, est_minutes: minutes,
      window_start: windowStart ? new Date(windowStart).toISOString() : null,
      window_end: windowEnd ? new Date(windowEnd).toISOString() : null };
    if (coords.trim()) {
      const parts = coords.split(",").map((p) => Number(p.trim()));
      if (parts.length !== 2 || parts.some((n) => Number.isNaN(n))) {
        setMessage("Toạ độ dạng: 21.0285, 105.8542 (dán từ Google Maps được)");
        return;
      }
      body.latitude = parts[0];
      body.longitude = parts[1];
    }
    try {
      await api.patch(`/field/cases/${item.id}/dispatch`, body);
      setMessage("Đã lưu");
      onSaved();
    } catch (err) {
      setMessage(getApiErrorMessage(err));
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900">Điều phối</h3>
        {item.scheduled_start && (
          <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] text-indigo-700">
            Lịch: {new Date(item.scheduled_start).toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" })}
            {item.route_order ? ` · điểm ${item.route_order}` : ""}
          </span>
        )}
      </div>
      {item.external_ref && <p className="mt-1 text-[11px] text-slate-500">Nguồn ngoài: {item.external_ref}</p>}
      <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
        <label className="col-span-2 flex flex-col gap-1">Toạ độ khách hàng
          <span className="flex gap-1">
            <input value={coords} onChange={(e) => setCoords(e.target.value)} placeholder="21.0285, 105.8542"
              className="flex-1 rounded-md border border-slate-200 px-2 py-1.5" />
            <button type="button" onClick={here} title="Dùng vị trí hiện tại" className="rounded-md border border-slate-200 px-2"><Crosshair className="h-4 w-4" /></button>
            {item.latitude != null && (
              <a href={`https://www.google.com/maps/dir/?api=1&destination=${item.latitude},${item.longitude}`} target="_blank" rel="noreferrer"
                title="Chỉ đường" className="flex items-center rounded-md border border-slate-200 px-2"><MapPin className="h-4 w-4" /></a>
            )}
          </span>
        </label>
        <label className="flex flex-col gap-1">Ưu tiên
          <select value={priority} onChange={(e) => setPriority(e.target.value as FieldPriority)} className="rounded-md border border-slate-200 px-2 py-1.5">
            {Object.entries(PRIORITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">Thời gian xử lý (phút)
          <input type="number" min={5} max={600} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} className="rounded-md border border-slate-200 px-2 py-1.5" />
        </label>
        <label className="flex flex-col gap-1">Hẹn khách từ
          <input type="datetime-local" value={windowStart} onChange={(e) => setWindowStart(e.target.value)} className="rounded-md border border-slate-200 px-2 py-1.5" />
        </label>
        <label className="flex flex-col gap-1">đến
          <input type="datetime-local" value={windowEnd} onChange={(e) => setWindowEnd(e.target.value)} className="rounded-md border border-slate-200 px-2 py-1.5" />
        </label>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button type="button" onClick={() => void save()} className="inline-flex items-center gap-1 rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white"><Save className="h-3.5 w-3.5" /> Lưu</button>
        {message && <span className="text-xs text-slate-600">{message}</span>}
      </div>
    </div>
  );
}
