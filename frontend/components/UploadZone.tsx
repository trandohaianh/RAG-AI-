"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { Upload, Trash2, FileText, MessageSquare } from "lucide-react";
import api, {
  API_URL,
  SESSION_HEADERS,
  getApiErrorMessage,
  type Conversation,
  type DocItem,
  type LineageMap,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import LineageBadge from "@/components/LineageBadge";
import { cn } from "@/lib/utils";

const ALLOWED_EXT = ["pdf", "docx", "doc", "txt", "png", "jpg", "jpeg", "gif", "webp"];

type FileRow = {
  name: string;
  status: "uploading" | "done" | "error";
  progress: number;
  chunks?: number;
  error?: string;
};

export default function UploadZone({
  docs,
  onRefresh,
  lineage = {},
}: {
  docs: DocItem[];
  onRefresh: () => void;
  lineage?: LineageMap;
}) {
  const router = useRouter();
  const [dragging, setDragging] = useState(false);
  const [rows, setRows] = useState<FileRow[]>([]);
  const [toast, setToast] = useState<string | null>(null);

  const uploadOne = useCallback(
    async (file: File) => {
      const ext = file.name.split(".").pop()?.toLowerCase();
      if (!ext) return;

      if (!ALLOWED_EXT.includes(ext)) {
        setRows((r) => [
          ...r,
          {
            name: file.name,
            status: "error",
            progress: 0,
            error: `Chỉ hỗ trợ: ${ALLOWED_EXT.join(", ").toUpperCase()}`,
          },
        ]);
        return;
      }

      setRows((r) => [...r, { name: file.name, status: "uploading", progress: 0 }]);

      const form = new FormData();
      form.append("file", file);
      try {
        await new Promise<void>((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          xhr.open("POST", `${API_URL}/documents/upload`);
          // Phiên nằm trong cookie httpOnly; header chống CSRF bắt buộc cho request ghi.
          xhr.withCredentials = true;
          for (const [name, value] of Object.entries(SESSION_HEADERS)) xhr.setRequestHeader(name, value);
          xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
              const pct = Math.round((e.loaded / e.total) * 100);
              setRows((prev) => prev.map((x) => (x.name === file.name ? { ...x, progress: pct } : x)));
            }
          };
          xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
              const data = JSON.parse(xhr.responseText);
              setRows((prev) =>
                prev.map((x) =>
                  x.name === file.name ? { ...x, status: "done", progress: 100, chunks: data.chunks } : x,
                ),
              );
              setToast(`${data.chunks} chunks đã được lập chỉ mục — bạn có thể hỏi ngay!`);
              setTimeout(() => setToast(null), 6000);
              onRefresh();
              resolve();
            } else {
              let msg = "Tải lên thất bại";
              try {
                const err = JSON.parse(xhr.responseText);
                if (typeof err.detail === "string") msg = err.detail;
              } catch {
                /* ignore */
              }
              reject(new Error(msg));
            }
          };
          xhr.onerror = () => reject(new Error("Lỗi mạng — kiểm tra backend đang chạy"));
          xhr.send(form);
        });
      } catch (e) {
        setRows((prev) =>
          prev.map((x) =>
            x.name === file.name ? { ...x, status: "error", error: e instanceof Error ? e.message : "Lỗi" } : x,
          ),
        );
      }
    },
    [onRefresh],
  );

  async function deleteDoc(name: string) {
    if (!confirm(`Xóa ${name}?`)) return;
    try {
      await api.delete(`/documents/${encodeURIComponent(name)}`);
      onRefresh();
    } catch (e) {
      alert(getApiErrorMessage(e));
    }
  }

  async function goToChat() {
    try {
      const res = await api.post<Conversation>("/conversations", { title: "Hỏi về tài liệu" });
      router.push(`/chat/${res.data.id}`);
    } catch (e) {
      alert(getApiErrorMessage(e));
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      {toast && (
        <div className="rounded-md bg-primary/10 text-primary text-sm px-4 py-2 border border-primary/20 flex items-center justify-between gap-2">
          <span>{toast}</span>
          <Button size="sm" variant="secondary" onClick={goToChat}>
            <MessageSquare className="h-4 w-4 mr-1" />
            Hỏi ngay
          </Button>
        </div>
      )}

      {docs.length > 0 && !toast && (
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-xs flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            Bạn có {docs.length} tài liệu đã lập chỉ mục. Sang Chat để hỏi về nội dung.
          </p>
          <Button onClick={goToChat}>
            <MessageSquare className="h-4 w-4 mr-2" />
            Hỏi về tài liệu
          </Button>
        </div>
      )}

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          Array.from(e.dataTransfer.files).forEach(uploadOne);
        }}
        className={cn(
          "relative border-2 border-dashed rounded-xl p-10 text-center transition-colors",
          dragging ? "border-primary bg-primary/5" : "border-slate-200 bg-slate-50/50",
        )}
      >
        <Upload className="h-10 w-10 mx-auto text-muted-foreground mb-3" />
        <p className="text-sm text-muted-foreground mb-1">
          PDF, DOCX, TXT — hoặc ảnh (PNG/JPG), chữ trong ảnh được đọc tự động
        </p>
        <input
          type="file"
          accept=".pdf,.docx,.doc,.txt,.png,.jpg,.jpeg,.gif,.webp"
          multiple
          className="absolute inset-0 opacity-0 cursor-pointer"
          onChange={(e) => Array.from(e.target.files ?? []).forEach(uploadOne)}
        />
        <Button variant="secondary" type="button">
          Chọn file
        </Button>
      </div>

      {rows.length > 0 && (
        <ul className="space-y-3">
          {rows.map((r) => (
            <li key={r.name} className="rounded-xl border border-slate-200 bg-white p-4 space-y-2 shadow-xs">
              <div className="flex items-center gap-2 text-sm">
                <FileText className="h-4 w-4 shrink-0" />
                <span className="flex-1 truncate">{r.name}</span>
                <span className="text-xs text-muted-foreground">
                  {r.status === "uploading" ? "Đang tải lên" : r.status === "done" ? "Xong" : "Lỗi"}
                </span>
              </div>
              {r.status === "uploading" && <Progress value={r.progress} />}
              {r.status === "done" && r.chunks != null && (
                <p className="text-xs text-green-600">
                  {r.chunks} đoạn đã lập chỉ mục
                  {/\.(png|jpe?g|gif|webp)$/i.test(r.name) ? " · đã đọc chữ trong ảnh" : ""}
                </p>
              )}
              {r.error && <p className="text-xs text-destructive">{r.error}</p>}
            </li>
          ))}
        </ul>
      )}

      <div>
        <h3 className="font-medium mb-3">Tài liệu của bạn</h3>
        <div className="rounded-xl border border-slate-200 bg-white overflow-hidden shadow-xs">
          <table className="w-full text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className="text-left p-3">Tên</th>
                <th className="text-left p-3">Số đoạn</th>
                <th className="text-left p-3">Ngày tải lên</th>
                <th className="text-left p-3">Trạng thái xử lý</th>
                <th className="p-3" />
              </tr>
            </thead>
            <tbody>
              {docs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="p-4 text-center text-muted-foreground">
                    Chưa có tài liệu — tải file lên ở trên
                  </td>
                </tr>
              ) : (
                docs.map((d) => (
                  <tr key={d.name} className="border-t">
                    <td className="p-3">{d.name}</td>
                    <td className="p-3">{d.chunks}</td>
                    <td className="p-3 text-muted-foreground">{d.uploaded_at ? d.uploaded_at.slice(0, 10) : "—"}</td>
                    <td className="p-3">
                      <LineageBadge lineage={lineage} filename={d.name} />
                    </td>
                    <td className="p-3 text-right">
                      <Button variant="ghost" size="icon" onClick={() => deleteDoc(d.name)}>
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
