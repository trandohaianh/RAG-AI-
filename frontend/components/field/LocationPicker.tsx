"use client";
import { useEffect, useRef, useState } from "react";
import { MapPinned, Search, X } from "lucide-react";
import "leaflet/dist/leaflet.css";
import api, { getApiErrorMessage, type FieldCase, type FieldGeocodeResult } from "@/lib/api";

type Source = "geocoded" | "suggestion" | "manual";
type Pin = { lat: number; lng: number };

const DEFAULT_CENTER: [number, number] = [21.0285, 105.8542];

/**
 * M13 — xác nhận vị trí khách trên bản đồ trước khi lưu (docs/design/M13_dia_chi_toa_do.md).
 * Gợi ý từ OpenStreetMap chỉ là điểm bắt đầu: trưởng đội kéo ghim tới đúng nhà rồi mới lưu.
 */
export default function LocationPicker({
  item,
  onClose,
  onSaved,
}: {
  item: FieldCase;
  onClose: () => void;
  onSaved: () => void;
}) {
  const suggestion = item.location_suggestion ?? null;
  const [address, setAddress] = useState(item.address ?? "");
  const [results, setResults] = useState<FieldGeocodeResult[]>([]);
  const [chosen, setChosen] = useState<number | null>(null);
  const [pin, setPin] = useState<Pin | null>(
    suggestion
      ? { lat: suggestion.latitude, lng: suggestion.longitude }
      : item.latitude != null && item.longitude != null
        ? { lat: item.latitude, lng: item.longitude }
        : null,
  );
  const [source, setSource] = useState<Source>(suggestion ? "suggestion" : "manual");
  const [message, setMessage] = useState("");
  // Mở hộp thoại: có gợi ý thì dùng gợi ý; chưa có gì mà có địa chỉ thì tra luôn một lần.
  const autoSearch = !suggestion && item.latitude == null && (item.address ?? "").trim().length >= 5;
  const [searching, setSearching] = useState(autoSearch);
  const [saving, setSaving] = useState(false);
  const holder = useRef<HTMLDivElement>(null);
  const mapRef = useRef<import("leaflet").Map | null>(null);
  const markerRef = useRef<import("leaflet").Marker | null>(null);
  // setPin/setSource ổn định nên hàm đặt ghim tạo một lần, dùng được trong sự kiện Leaflet.
  const placeRef = useRef((p: Pin, next: Source) => {
    setPin(p);
    setSource(next);
  });
  // Kéo ghim giữ nguồn hiện tại (gợi ý / tra địa chỉ / tay) — đọc qua ref để không cũ.
  const sourceRef = useRef<Source>(source);
  useEffect(() => {
    sourceRef.current = source;
  }, [source]);

  type GeocodeReply = { results: FieldGeocodeResult[]; message: string | null };
  const requestRef = useRef(async (text: string) => {
    const { data } = await api.post<GeocodeReply>(`/field/workspaces/${item.workspace_id}/geocode`, {
      address: text,
    });
    return data;
  });
  const applyRef = useRef((data: GeocodeReply) => {
    setResults(data.results);
    if (data.results.length) {
      setChosen(0);
      placeRef.current({ lat: data.results[0].latitude, lng: data.results[0].longitude }, "geocoded");
    } else {
      setChosen(null);
      setMessage(data.message ?? "Không tìm thấy — hãy bấm lên bản đồ để đặt ghim.");
    }
  });

  async function search() {
    const text = address.trim();
    if (text.length < 5) return setMessage("Nhập địa chỉ đầy đủ hơn để tra.");
    setSearching(true);
    setMessage("");
    try {
      applyRef.current(await requestRef.current(text));
    } catch (err) {
      setMessage(getApiErrorMessage(err));
    } finally {
      setSearching(false);
    }
  }

  useEffect(() => {
    if (!autoSearch) return;
    let alive = true;
    requestRef
      .current((item.address ?? "").trim())
      .then((data) => alive && applyRef.current(data))
      .catch((err) => alive && setMessage(getApiErrorMessage(err)))
      .finally(() => alive && setSearching(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- chỉ chạy khi mở hộp thoại
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || !holder.current || mapRef.current) return;
      const map = L.map(holder.current).setView(pin ? [pin.lat, pin.lng] : DEFAULT_CENTER, pin ? 17 : 12);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors",
      }).addTo(map);
      map.on("click", (event) => placeRef.current({ lat: event.latlng.lat, lng: event.latlng.lng }, "manual"));
      mapRef.current = map;
      setTimeout(() => map.invalidateSize(), 50);
    })();
    return () => {
      cancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
      markerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- tạo bản đồ một lần
  }, []);

  // Đồng bộ ghim với state (kéo ghim / bấm bản đồ / chọn gợi ý).
  useEffect(() => {
    void (async () => {
      const L = (await import("leaflet")).default;
      const map = mapRef.current;
      if (!map || !pin) return;
      if (!markerRef.current) {
        // divIcon: không phụ thuộc ảnh icon mặc định của Leaflet (hay vỡ đường dẫn khi đóng gói).
        const icon = L.divIcon({
          className: "",
          html: '<div style="width:22px;height:22px;border-radius:50% 50% 50% 0;background:#dc2626;transform:rotate(-45deg);border:3px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.4)"></div>',
          iconSize: [22, 22],
          iconAnchor: [11, 22],
        });
        markerRef.current = L.marker([pin.lat, pin.lng], { draggable: true, icon }).addTo(map);
        markerRef.current.on("dragend", () => {
          const at = markerRef.current!.getLatLng();
          placeRef.current({ lat: at.lat, lng: at.lng }, sourceRef.current);
        });
      } else {
        markerRef.current.setLatLng([pin.lat, pin.lng]);
      }
      if (!map.getBounds().contains([pin.lat, pin.lng])) map.setView([pin.lat, pin.lng], Math.max(map.getZoom(), 16));
    })();
  }, [pin]);

  async function save() {
    if (!pin) return setMessage("Chưa có vị trí — tra địa chỉ hoặc bấm lên bản đồ để đặt ghim.");
    setSaving(true);
    setMessage("");
    try {
      await api.patch(`/field/cases/${item.id}/dispatch`, {
        latitude: Number(pin.lat.toFixed(6)),
        longitude: Number(pin.lng.toFixed(6)),
        location_source: source,
      });
      onSaved();
    } catch (err) {
      setMessage(getApiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  const precision =
    source === "geocoded" && chosen != null
      ? results[chosen]?.precision
      : source === "suggestion"
        ? suggestion?.precision
        : null;
  const precisionLabel =
    source === "geocoded" && chosen != null
      ? results[chosen]?.precision_label
      : source === "suggestion"
        ? suggestion?.precision_label
        : null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/50 p-4"
    >
      <div className="flex max-h-[94vh] w-full max-w-2xl flex-col overflow-y-auto rounded-2xl bg-white p-5 shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold text-slate-900">
            <MapPinned className="h-4 w-4" /> Xác nhận vị trí khách — ca #{item.id}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Đóng hộp thoại"
            className="rounded p-1 hover:bg-slate-100"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {suggestion && source === "suggestion" && (
          <p className="mt-3 rounded-lg bg-indigo-50 p-2 text-xs text-indigo-900">
            Vị trí đề xuất{" "}
            {suggestion.source === "gps"
              ? "do kỹ thuật viên gửi (GPS tại nhà khách)"
              : suggestion.source === "geocoded"
                ? "tự tra từ địa chỉ"
                : "do kỹ thuật viên chọn trên bản đồ"}
            . Kiểm tra ghim rồi bấm Lưu để dùng cho xếp tuyến.
          </p>
        )}
        <div className="mt-3 flex gap-2">
          <input
            value={address}
            maxLength={300}
            onChange={(e) => setAddress(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void search()}
            placeholder="Số nhà, đường, phường, tỉnh/thành"
            aria-label="Địa chỉ cần tra"
            className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={() => void search()}
            disabled={searching}
            className="inline-flex items-center gap-1 rounded-lg bg-slate-900 px-3 py-2 text-xs font-medium text-white disabled:opacity-50"
          >
            <Search className="h-3.5 w-3.5" /> {searching ? "Đang tra…" : "Tìm toạ độ"}
          </button>
        </div>
        {results.length > 1 && (
          <div className="mt-2 flex flex-col gap-1">
            {results.map((r, i) => (
              <button
                key={`${r.latitude},${r.longitude}`}
                type="button"
                onClick={() => {
                  setChosen(i);
                  placeRef.current({ lat: r.latitude, lng: r.longitude }, "geocoded");
                }}
                className={`rounded-md border px-2 py-1 text-left text-xs ${chosen === i ? "border-slate-900 bg-slate-50" : "border-slate-200"}`}
              >
                {r.label} <span className="text-slate-500">· {r.precision_label}</span>
              </button>
            ))}
          </div>
        )}
        {precisionLabel && (
          <p
            className={`mt-2 rounded-lg p-2 text-xs ${precision === "house" ? "bg-emerald-50 text-emerald-900" : "bg-amber-50 text-amber-900"}`}
          >
            {precision === "house" ? precisionLabel : `⚠ ${precisionLabel} — hãy kéo ghim tới đúng nhà khách.`}
          </p>
        )}
        <div
          ref={holder}
          aria-label="Bản đồ chọn vị trí: bấm để đặt ghim, kéo ghim để chỉnh"
          className="mt-3 h-80 w-full shrink-0 rounded-lg border border-slate-200"
        />
        <p className="mt-1 text-[11px] text-slate-500">
          Bấm lên bản đồ để đặt ghim, kéo ghim để chỉnh. Dữ liệu bản đồ © OpenStreetMap contributors.
        </p>
        {pin && (
          <p className="mt-1 text-xs text-slate-700">
            Toạ độ: {pin.lat.toFixed(6)}, {pin.lng.toFixed(6)}
          </p>
        )}
        {message && (
          <p role="alert" className="mt-2 text-xs text-red-600">
            {message}
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg border border-slate-200 px-3 py-2 text-xs">
            Huỷ
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving || !pin}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-50"
          >
            {saving ? "Đang lưu…" : "Lưu vị trí này"}
          </button>
        </div>
      </div>
    </div>
  );
}
