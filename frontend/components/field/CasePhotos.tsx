"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Camera, ImageOff, LoaderCircle, Trash2, X } from "lucide-react";
import api, { getApiErrorMessage, type FieldPhoto } from "@/lib/api";

/** Ảnh hiện trường của một ca: chụp/chọn ảnh, chú thích, xem lớn, xoá (người chụp hoặc trưởng đội). */
export default function CasePhotos({ caseId, currentUserId, isOwner }: { caseId: number; currentUserId?: number; isOwner: boolean }) {
  const [photos, setPhotos] = useState<FieldPhoto[]>([]);
  const [caption, setCaption] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [viewing, setViewing] = useState<FieldPhoto | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setPhotos((await api.get<FieldPhoto[]>(`/field/cases/${caseId}/photos`)).data);
    } catch (err) {
      const offline = !(err as { response?: unknown })?.response;
      setError(offline ? "Đang offline — ảnh sẽ hiện và tải lên được khi có mạng." : getApiErrorMessage(err));
    }
  }, [caseId]);

  useEffect(() => { setPhotos([]); setError(""); void load(); }, [load]);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setError("");
    try {
      for (const file of Array.from(files)) {
        const form = new FormData();
        form.append("file", file);
        form.append("caption", caption);
        form.append("client_action_id", `photo-${crypto.randomUUID()}`);
        await api.post(`/field/cases/${caseId}/photos`, form);
      }
      setCaption("");
      await load();
    } catch (err) {
      setError(getApiErrorMessage(err));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  async function remove(photo: FieldPhoto) {
    if (!confirm("Xoá ảnh này?")) return;
    try {
      await api.delete(`/field/photos/${photo.id}`);
      setViewing(null);
      await load();
    } catch (err) {
      setError(getApiErrorMessage(err));
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900">Ảnh hiện trường</h3>
        <span className="text-[11px] text-slate-500">{photos.length} ảnh · đã xoá vị trí GPS</span>
      </div>
      {error && <p className="mt-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</p>}
      <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-4">
        {photos.map((photo) => (
          <button key={photo.id} type="button" onClick={() => setViewing(photo)}
            className="group relative aspect-square overflow-hidden rounded-lg border border-slate-200 bg-slate-100">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={photo.thumb_url} alt={photo.caption || `Ảnh #${photo.id}`} className="h-full w-full object-cover" loading="lazy" />
            {photo.caption && <span className="absolute inset-x-0 bottom-0 truncate bg-black/50 px-1.5 py-0.5 text-[10px] text-white">{photo.caption}</span>}
          </button>
        ))}
        {!photos.length && (
          <div className="col-span-3 flex items-center gap-2 rounded-lg border border-dashed border-slate-300 p-3 text-xs text-slate-500 sm:col-span-4">
            <ImageOff className="h-4 w-4" /> Chưa có ảnh. Chụp trước/sau khi xử lý để làm bằng chứng.
          </div>
        )}
      </div>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row">
        <input value={caption} onChange={(e) => setCaption(e.target.value)} maxLength={300} placeholder="Chú thích (vd. Đầu ONT trước khi thay)"
          className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm" />
        <label className={`inline-flex cursor-pointer items-center justify-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white ${busy ? "opacity-60" : ""}`}>
          {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Camera className="h-4 w-4" />}
          Chụp / chọn ảnh
          <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" capture="environment" multiple hidden
            disabled={busy} onChange={(e) => void upload(e.target.files)} />
        </label>
      </div>
      {viewing && (
        <div role="dialog" aria-modal className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={() => setViewing(null)}>
          <div className="max-h-full max-w-4xl" onClick={(e) => e.stopPropagation()}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={viewing.url} alt={viewing.caption} className="max-h-[80vh] rounded-lg object-contain" />
            <div className="mt-2 flex items-center justify-between gap-3 text-sm text-white">
              <span>{viewing.caption || "Không có chú thích"} · {new Date(viewing.created_at).toLocaleString("vi-VN")}</span>
              <span className="flex gap-2">
                {(viewing.uploaded_by === currentUserId || isOwner) && (
                  <button type="button" onClick={() => void remove(viewing)} className="inline-flex items-center gap-1 rounded-md bg-rose-600 px-2.5 py-1.5"><Trash2 className="h-4 w-4" /> Xoá</button>
                )}
                <button type="button" onClick={() => setViewing(null)} className="inline-flex items-center gap-1 rounded-md bg-white/20 px-2.5 py-1.5"><X className="h-4 w-4" /> Đóng</button>
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
