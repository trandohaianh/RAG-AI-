"use client";
import { useState } from "react";
import type { Evidence, CitationAudit, LineageMap } from "@/lib/api";
import LineageBadge from "@/components/LineageBadge";

type Props = {
  evidence?: Evidence[];
  audit?: CitationAudit | null;
  lineage?: LineageMap;
  checkVersion?: (reference: string) => Promise<string>;
};

export default function EvidencePanel({ evidence = [], audit, lineage = {}, checkVersion }: Props) {
  const [states, setStates] = useState<Record<string, string>>({});
  if (!evidence.length) return null;
  const check = async (reference: string) => {
    setStates((value) => ({ ...value, [reference]: "checking" }));
    try {
      const status = await checkVersion!(reference);
      setStates((value) => ({ ...value, [reference]: status }));
    } catch {
      setStates((value) => ({ ...value, [reference]: "unavailable" }));
    }
  };
  const labels: Record<string, string> = {
    checking: "Đang kiểm tra…",
    current: "Lần kiểm tra: phiên bản chưa thay đổi",
    changed: "Kho tài liệu đã cập nhật; đây là đoạn trích lúc trả lời",
    unverified: "Chưa xác định được phiên bản",
    unavailable: "Chưa kiểm tra được phiên bản",
  };
  return (
    <section className="mt-3 space-y-2 border-t border-slate-200 pt-3" aria-label="Bằng chứng tài liệu">
      <p className="text-xs font-semibold text-slate-700">Đoạn trích tài liệu</p>
      {audit?.status === "uncited" && (
        <p className="text-xs text-amber-700">Câu trả lời chưa dẫn đến đoạn nguồn cụ thể.</p>
      )}
      {evidence.map((item) => (
        <details key={item.reference_id} className="rounded-lg border border-slate-200 bg-slate-50 p-2">
          <summary className="cursor-pointer text-xs font-medium text-slate-700">
            【{item.reference_id}】 {item.source}
            {item.page ? ` · trang ${item.page}` : ""}
            {audit?.cited_references.includes(item.reference_id) && (
              <span className="ml-2 text-indigo-600">Đã dẫn</span>
            )}
          </summary>
          <blockquote className="my-2 max-h-56 overflow-y-auto whitespace-pre-wrap border-l-2 border-indigo-200 pl-2 text-xs leading-relaxed text-slate-700">
            {item.quote}
          </blockquote>
          {lineage[item.source] && <LineageBadge lineage={lineage} filename={item.source} />}
          {checkVersion && (
            <button
              type="button"
              className="mt-1 block text-xs text-indigo-600 underline"
              onClick={() => check(item.reference_id)}
              disabled={states[item.reference_id] === "checking"}
            >
              Kiểm tra phiên bản
            </button>
          )}
          {states[item.reference_id] && (
            <p className="mt-1 text-xs text-slate-500" role="status">
              {labels[states[item.reference_id]] || labels.unavailable}
            </p>
          )}
        </details>
      ))}
      <p className="text-[11px] text-slate-500">Đoạn trích được lưu lúc trả lời để bạn đối chiếu.</p>
    </section>
  );
}
