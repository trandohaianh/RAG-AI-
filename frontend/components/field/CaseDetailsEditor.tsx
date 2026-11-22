"use client";
import { useEffect, useState } from "react";
import axios from "axios";
import { Pencil, RotateCw, Save, X } from "lucide-react";
import api, { getApiErrorMessage, type FieldCase } from "@/lib/api";

/** M14 — trưởng đội sửa thông tin ca (docs/design/M14_sua_thong_tin_ca.md). */
type DetailKey = "title" | "symptom" | "customer_label" | "address" | "device_model" | "serial_number";

const FIELDS: { key: DetailKey; label: string; required?: boolean; multiline?: boolean; max: number }[] = [
  { key: "title", label: "Tiêu đề", required: true, max: 255 },
  { key: "customer_label", label: "Khách hàng", max: 160 },
  { key: "address", label: "Địa chỉ", max: 500 },
  { key: "device_model", label: "Model thiết bị", max: 160 },
  { key: "serial_number", label: "Số serial", max: 160 },
  { key: "symptom", label: "Triệu chứng / thông tin bàn giao", required: true, multiline: true, max: 5000 },
];

type Props = {
  item: FieldCase;
  /** Tải lại ca sau khi lưu (hoặc khi người dùng bấm "Tải lại" lúc xung đột). */
  onSaved: () => void;
  /** Xếp thao tác vào hàng đợi offline khi mất mạng (cùng cơ chế với các thao tác ca khác). */
  queue: (method: "patch", url: string, body: Record<string, unknown>, label: string) => Promise<boolean>;
};

function current(item: FieldCase): Record<DetailKey, string> {
  return Object.fromEntries(FIELDS.map((f) => [f.key, (item[f.key] as string | null) ?? ""])) as Record<
    DetailKey,
    string
  >;
}

export default function CaseDetailsEditor({ item, onSaved, queue }: Props) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Record<DetailKey, string>>(() => current(item));
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const [saving, setSaving] = useState(false);

  // Đổi sang ca khác, hoặc ca được tải lại với thông tin mới: bỏ bản nháp cũ.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- đồng bộ form với ca đang chọn
    setForm(current(item));
    setMessage("");
    setConflict(false);
  }, [item.id, item.details_version]); // eslint-disable-line react-hooks/exhaustive-deps

  const resolved = item.status === "resolved";

  async function save() {
    setMessage("");
    setConflict(false);
    const before = current(item);
    const changed = FIELDS.filter((f) => form[f.key].trim() !== before[f.key].trim());
    if (changed.length === 0) {
      setEditing(false);
      return;
    }
    for (const f of FIELDS) {
      if (f.required && form[f.key].trim().length < 3) {
        setMessage(`${f.label} cần ít nhất 3 ký tự`);
        return;
      }
    }
    const body: Record<string, unknown> = {
      expected_version: item.details_version,
      client_action_id: crypto.randomUUID(),
    };
    for (const f of changed) body[f.key] = form[f.key].trim() || null;
    const url = `/field/cases/${item.id}/details`;
    setSaving(true);
    try {
      await api.patch(url, body);
      setEditing(false);
      onSaved();
    } catch (err) {
      if (axios.isAxiosError(err) && !err.response) {
        // Mất mạng: giữ thao tác trong máy, tự gửi lại (cùng client_action_id nên không áp dụng 2 lần).
        if (await queue("patch", url, body, `Sửa thông tin ca #${item.id}`)) setEditing(false);
      } else if (axios.isAxiosError(err) && err.response?.status === 409) {
        setConflict(!resolved);
        setMessage(getApiErrorMessage(err));
      } else {
        setMessage(getApiErrorMessage(err));
      }
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setEditing(true)}
          disabled={resolved}
          title={resolved ? "Ca đã hoàn tất — mở lại ca trước khi sửa thông tin" : undefined}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Pencil className="h-3.5 w-3.5" /> Sửa thông tin ca
        </button>
        {resolved && <span className="text-[11px] text-slate-500">Ca đã hoàn tất: mở lại ca để sửa.</span>}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-200 p-4">
      <h3 className="text-sm font-semibold text-slate-900">Sửa thông tin ca</h3>
      <p className="mt-1 text-[11px] text-slate-500">
        Mỗi lần lưu được ghi vào nhật ký ca. Đổi địa chỉ sẽ xoá toạ độ cũ — nhập lại toạ độ ở mục Điều phối.
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {FIELDS.map((f) => (
          <label key={f.key} className={f.multiline ? "sm:col-span-2 text-xs" : "text-xs"}>
            <span className="font-medium text-slate-700">
              {f.label}
              {f.required ? " *" : ""}
            </span>
            {f.multiline ? (
              <textarea
                value={form[f.key]}
                maxLength={f.max}
                rows={4}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
              />
            ) : (
              <input
                value={form[f.key]}
                maxLength={f.max}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
              />
            )}
          </label>
        ))}
      </div>
      {message && (
        <p role="alert" className="mt-3 text-xs text-red-600">
          {message}
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          <Save className="h-3.5 w-3.5" /> {saving ? "Đang lưu…" : "Lưu"}
        </button>
        {conflict && (
          <button
            type="button"
            onClick={() => onSaved()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-900"
          >
            <RotateCw className="h-3.5 w-3.5" /> Tải lại bản mới
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            setForm(current(item));
            setMessage("");
            setConflict(false);
            setEditing(false);
          }}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          <X className="h-3.5 w-3.5" /> Huỷ
        </button>
      </div>
    </div>
  );
}
