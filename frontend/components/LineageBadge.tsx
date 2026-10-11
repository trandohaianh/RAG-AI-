"use client";

import { ShieldCheck, ShieldAlert, ShieldQuestion } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DocLineage, LineageMap, LineageStatus } from "@/lib/api";

function isDocLineage(v: DocLineage | LineageStatus | undefined): v is DocLineage {
  return !!v && "validation_passed" in v;
}

type Tone = "success" | "warning" | "muted";

function resolve(lineage: LineageMap, filename: string): { label: string; tone: Tone; title: string } {
  const status = lineage.__status__ as LineageStatus | undefined;

  if (!status?.medallion_enabled) {
    return {
      label: "Pipeline: tắt",
      tone: "muted",
      title: "Data Engineering pipeline (Bronze/Silver/Gold + Great Expectations) chưa được bật trên server này.",
    };
  }
  if (status.reachable === false) {
    return {
      label: "Không kết nối kho dữ liệu",
      tone: "muted",
      title: "Không kết nối được Postgres warehouse để tra cứu lineage.",
    };
  }

  const entry = lineage[filename];
  if (!isDocLineage(entry)) {
    return {
      label: "Chưa qua pipeline",
      tone: "muted",
      title: "Tài liệu này chưa được ghi nhận trong kho Bronze/Silver/Gold.",
    };
  }

  if (entry.validation_passed === true && entry.gold_ready) {
    return {
      label: "Đã kiểm định · Gold",
      tone: "success",
      title: `Đã qua Great Expectations (${entry.silver_chunk_count} chunks) và sẵn sàng ở tầng Gold.`,
    };
  }
  if (entry.validation_passed === false) {
    return {
      label: "Chưa qua kiểm định",
      tone: "warning",
      title: "Great Expectations phát hiện vấn đề chất lượng dữ liệu (chunk rỗng/trùng lặp/độ dài bất thường) — dữ liệu bị chặn ở tầng Silver, chưa lên Gold.",
    };
  }
  return {
    label: "Đang xử lý",
    tone: "muted",
    title: "Đã ghi nhận ở Bronze, đang chờ kiểm định.",
  };
}

const TONE_CLASS: Record<Tone, string> = {
  success: "text-success border-success/30 bg-success/10",
  warning: "text-amber-600 border-amber-500/30 bg-amber-500/10",
  muted: "text-muted-foreground border-slate-200 bg-slate-50",
};

const TONE_ICON: Record<Tone, typeof ShieldCheck> = {
  success: ShieldCheck,
  warning: ShieldAlert,
  muted: ShieldQuestion,
};

export default function LineageBadge({ lineage, filename }: { lineage: LineageMap; filename: string }) {
  const { label, tone, title } = resolve(lineage, filename);
  const Icon = TONE_ICON[tone];
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium whitespace-nowrap",
        TONE_CLASS[tone]
      )}
    >
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}
