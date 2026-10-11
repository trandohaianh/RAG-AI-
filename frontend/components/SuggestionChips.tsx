"use client";

import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import api from "@/lib/api";
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
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      // Gợi ý sinh bằng LLM; với Ollama chạy CPU có thể mất hơn 15s mặc định.
      .get<{ questions: string[] }>("/documents/suggested-questions?limit=6", { timeout: 120000 })
      .then((res) => {
        if (!cancelled) {
          setFromDoc(Array.isArray(res.data?.questions) ? res.data.questions : []);
        }
      })
      .catch(() => {
        // Gợi ý chỉ là phần phụ: lỗi thì giữ các câu gợi ý mặc định, không báo đỏ.
        if (!cancelled) setFromDoc([]);
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
    <div className="px-1 pb-2.5 space-y-1.5 shrink-0">
      <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <Sparkles className="h-3.5 w-3.5 text-primary" />
        Gợi ý câu hỏi
        {loading && <span className="font-normal">— đang tải từ tài liệu…</span>}
      </div>
      {fromDoc.length > 0 && (
        <p className="text-xs text-primary">Có {fromDoc.length} gợi ý từ tài liệu đã tải lên</p>
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
