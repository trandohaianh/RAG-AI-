"use client";

import { useCallback, useEffect, useState } from "react";
import UploadZone from "@/components/UploadZone";
import api, { getDocLineage, type DocItem, type LineageMap } from "@/lib/api";

export default function DocsPage() {
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [lineage, setLineage] = useState<LineageMap>({});

  const load = useCallback(async () => {
    try {
      const res = await api.get<DocItem[]>("/documents");
      setDocs(Array.isArray(res.data) ? res.data : []);
    } catch {
      setDocs([]);
    }
    try {
      setLineage(await getDocLineage());
    } catch {
      setLineage({});
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6 space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Tài liệu cá nhân</h1>
        <p className="text-sm text-muted-foreground mt-1">
          PDF, DOCX, TXT và <strong className="text-foreground">ảnh</strong> (PNG/JPG) — chữ trong ảnh được đọc tự động
        </p>
      </div>

      {/* Trang này chỉ nạp vào kho CÁ NHÂN (dùng cho mục Trợ lý tài liệu). Trợ lý trong từng ca
          hiện trường đọc kho của ĐỘI, được upload ở trang Ca hiện trường — trước đây trang này
          ghi "Tài liệu của đội" nên SOP upload ở đây không bao giờ tới được trợ lý ca. */}
      <div
        role="note"
        className="rounded-lg border border-amber-300/60 bg-amber-50 dark:bg-amber-950/30 p-4 text-sm max-w-3xl"
      >
        <p className="font-medium text-amber-900 dark:text-amber-200">Tài liệu ở đây chỉ dành cho bạn</p>
        <p className="text-amber-900/80 dark:text-amber-200/80 mt-1">
          Dùng cho mục <strong>Trợ lý tài liệu</strong>. SOP/manual cho cả đội (trợ lý trong từng ca sửa dùng) hãy tải
          lên ở{" "}
          <a href="/field" className="underline font-medium">
            Ca hiện trường → Tri thức dùng chung
          </a>
          .
        </p>
      </div>

      <div className="rounded-lg border border-primary/30 bg-primary/5 p-4 text-sm space-y-2 max-w-3xl">
        <p className="font-medium text-primary">📷 Đọc chữ từ ảnh (OCR)</p>
        <p className="text-muted-foreground">
          Kéo thả hoặc chọn file <strong>PNG, JPG, JPEG, WebP, GIF</strong> — hệ thống dùng Gemini đọc chữ trong ảnh,
          rồi lập chỉ mục như văn bản. Sau đó sang <strong>Chat</strong> để hỏi nội dung ảnh.
        </p>
      </div>

      <UploadZone docs={docs} onRefresh={load} lineage={lineage} />
    </div>
  );
}
