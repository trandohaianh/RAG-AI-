"use client";
import { useState } from "react";
import axios from "axios";
import { FileSignature, X } from "lucide-react";
import api, { getApiErrorMessage, type FieldCase } from "@/lib/api";
import SignatureCanvas, { type Stroke } from "@/components/field/SignatureCanvas";

/** M12 — 6 mã kết quả cố định (khớp backend/field/schemas.py CLOSURE_OUTCOMES). */
export const OUTCOMES: { code: string; label: string; noSignature?: boolean }[] = [
  { code: "fixed", label: "Đã sửa xong" },
  { code: "replaced", label: "Thay thiết bị" },
  { code: "not_reproduced", label: "Không tái hiện lỗi" },
  { code: "escalated", label: "Chuyển cấp kỹ thuật" },
  { code: "customer_absent", label: "Khách vắng nhà", noSignature: true },
  { code: "customer_refused", label: "Khách từ chối sửa", noSignature: true },
];

type DialogProps = {
  item: FieldCase;
  onClose: () => void;
  onClosed: () => void;
  queue: (method: "post", url: string, body: Record<string, unknown>, label: string) => Promise<boolean>;
};

/** Hộp thoại biên bản đóng ca: mã kết quả, ghi chú, người ký, chữ ký (docs/design/M12_bien_ban_dong_ca.md). */
export function CloseCaseDialog({ item, onClose, onClosed, queue }: DialogProps) {
  const [outcome, setOutcome] = useState("fixed");
  const [note, setNote] = useState("");
  const [customer, setCustomer] = useState(item.customer_label ?? "");
  const [strokes, setStrokes] = useState<Stroke[]>([]);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const noSignature = OUTCOMES.find((o) => o.code === outcome)?.noSignature ?? false;

  async function submit() {
    setMessage("");
    if (note.trim().length < 3) return setMessage("Cần ghi chú kết quả (ít nhất 3 ký tự).");
    if (!noSignature && strokes.length === 0) return setMessage("Cần chữ ký của khách hàng.");
    const body: Record<string, unknown> = {
      outcome_code: outcome,
      note: note.trim(),
      customer_name: customer.trim() || null,
      signature: noSignature ? null : strokes,
      client_action_id: crypto.randomUUID(),
    };
    const url = `/field/cases/${item.id}/close`;
    setSaving(true);
    try {
      await api.post(url, body);
      onClosed();
    } catch (err) {
      if (axios.isAxiosError(err) && !err.response) {
        // Mất mạng: biên bản (kể cả nét ký) được giữ trong máy và gửi lại khi có mạng.
        if (await queue("post", url, body, `Đóng ca #${item.id}`)) onClosed();
      } else {
        setMessage(getApiErrorMessage(err));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
    >
      <div className="max-h-[92vh] w-full max-w-xl overflow-y-auto rounded-2xl bg-white p-5 shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold text-slate-900">
            <FileSignature className="h-4 w-4" /> Biên bản đóng ca #{item.id}
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
        <label className="mt-4 block text-xs font-medium text-slate-700">
          Kết quả *
          <select
            value={outcome}
            onChange={(e) => setOutcome(e.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
          >
            {OUTCOMES.map((o) => (
              <option key={o.code} value={o.code}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="mt-3 block text-xs font-medium text-slate-700">
          Ghi chú kết quả *
          <textarea
            value={note}
            maxLength={2000}
            rows={3}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Đã làm gì, thay gì, tình trạng khi rời đi…"
            className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
          />
        </label>
        {noSignature ? (
          <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-900">
            Không có khách để ký: ca cần ít nhất 1 ảnh hiện trường làm bằng chứng (chụp ở mục Ảnh hiện trường).
          </p>
        ) : (
          <>
            <label className="mt-3 block text-xs font-medium text-slate-700">
              Người ký
              <input
                value={customer}
                maxLength={120}
                onChange={(e) => setCustomer(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
              />
            </label>
            <div className="mt-3">
              <div className="flex items-center justify-between text-xs font-medium text-slate-700">
                <span>Chữ ký khách hàng *</span>
                <button type="button" onClick={() => setStrokes([])} className="text-slate-500 underline">
                  Ký lại
                </button>
              </div>
              <div className="mt-1">
                <SignatureCanvas strokes={strokes} onChange={setStrokes} />
              </div>
            </div>
          </>
        )}
        {message && (
          <p role="alert" className="mt-3 text-xs text-red-600">
            {message}
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg border border-slate-200 px-3 py-2 text-xs">
            Huỷ
          </button>
          <button
            type="button"
            onClick={() => void submit()}
            disabled={saving}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-50"
          >
            {saving ? "Đang lưu…" : "Đóng ca"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Biên bản hiệu lực của ca đã đóng. */
export function ClosureSummary({ item }: { item: FieldCase }) {
  const closure = item.closure;
  if (!closure) return null;
  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4 text-sm">
      <h3 className="flex items-center gap-2 font-semibold text-emerald-900">
        <FileSignature className="h-4 w-4" /> Biên bản đóng ca
      </h3>
      <p className="mt-2 text-slate-800">
        <span className="font-medium">{closure.outcome_label}</span> ·{" "}
        {new Date(closure.closed_at).toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" })}
      </p>
      <p className="mt-1 whitespace-pre-wrap text-slate-700">{closure.note}</p>
      <p className="mt-2 text-xs text-slate-600">
        {closure.signed
          ? `Có chữ ký khách${closure.customer_name ? ` (${closure.customer_name})` : ""}`
          : "Không có chữ ký (khách vắng / từ chối)"}
      </p>
      {closure.signature_url && (
        // eslint-disable-next-line @next/next/no-img-element -- link ký có hạn từ API, không qua next/image
        <img
          src={closure.signature_url}
          alt="Chữ ký khách hàng"
          className="mt-2 w-full max-w-sm rounded border border-slate-200 bg-white"
        />
      )}
    </div>
  );
}
