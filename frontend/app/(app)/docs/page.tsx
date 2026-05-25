"use client";

import { useCallback, useEffect, useState } from "react";
import UploadZone from "@/components/UploadZone";
import api, { type DocItem } from "@/lib/api";

export default function DocsPage() {
  const [docs, setDocs] = useState<DocItem[]>([]);

  const load = useCallback(async () => {
    try {
      const res = await api.get<DocItem[]>("/documents");
      setDocs(Array.isArray(res.data) ? res.data : []);
    } catch {
      setDocs([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6 space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Quản lý tài liệu</h1>
        <p className="text-sm text-muted-foreground mt-1">
          PDF, DOCX, TXT và <strong className="text-foreground">ảnh OCR</strong> (PNG/JPG) — tuần 3–4
        </p>
      </div>

      <div className="rounded-lg border border-primary/30 bg-primary/5 p-4 text-sm space-y-2 max-w-3xl">
        <p className="font-medium text-primary">📷 OCR ảnh (tuần 3–4)</p>
        <p className="text-muted-foreground">
          Kéo thả hoặc chọn file <strong>PNG, JPG, JPEG, WebP, GIF</strong> — hệ thống dùng Gemini
          đọc chữ trong ảnh, rồi index như văn bản. Sau đó sang <strong>Chat</strong> để hỏi nội dung
          ảnh.
        </p>
      </div>

      <UploadZone docs={docs} onRefresh={load} />
    </div>
  );
}
