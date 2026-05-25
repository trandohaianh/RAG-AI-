"use client";

import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import api, { getApiErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";

const STATIC = [
  "Tóm tắt nội dung chính của tài liệu?",
  "Có những điểm quan trọng nào?",
  "Giải thích khái niệm then chốt?",
];

type Props = {
  onPick: (text: string) => void;
  disabled?: boolean;
};

export default function SuggestionChips({ onPick, disabled }: Props) {
  const [fromDoc, setFromDoc] = useState<string[]>([]);
  const [apiError, setApiError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setApiError(null);
    api
      .get<{ questions: string[] }>("/documents/suggested-questions?limit=6")
      .then((res) => {
        if (!cancelled) {
          setFromDoc(Array.isArray(res.data?.questions) ? res.data.questions : []);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setFromDoc([]);
          setApiError(getApiErrorMessage(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const chips = [...STATIC, ...fromDoc].filter((v, i, a) => a.indexOf(v) === i);

  return (
    <div className="border-t border-border bg-muted/30 px-4 py-3 space-y-2 shrink-0">
      <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <Sparkles className="h-3.5 w-3.5 text-primary" />
        Gợi ý câu hỏi (tuần 3–4)
        {loading && <span className="font-normal">— đang tải từ tài liệu…</span>}
      </div>
      {apiError && (
        <p className="text-xs text-destructive">
          {apiError} — kiểm tra backend: http://127.0.0.1:8000
        </p>
      )}
      {!apiError && fromDoc.length > 0 && (
        <p className="text-xs text-primary">Có {fromDoc.length} gợi ý từ file đã upload</p>
      )}
      <div className="flex flex-wrap gap-2">
        {chips.map((s) => (
          <Button
            key={s}
            type="button"
            variant="outline"
            size="sm"
            className="text-xs h-auto py-1.5 whitespace-normal text-left max-w-full"
            disabled={disabled}
            onClick={() => onPick(s)}
          >
            {s}
          </Button>
        ))}
      </div>
    </div>
  );
}
