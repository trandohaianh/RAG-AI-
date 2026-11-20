"use client";

import { useState } from "react";
import MarkdownContent from "@/components/MarkdownContent";
import { ChevronDown, ChevronRight, ThumbsDown, ThumbsUp } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import LineageBadge from "@/components/LineageBadge";
import EvidencePanel from "@/components/EvidencePanel";
import api, { type EvidenceBundle } from "@/lib/api";
import type { LineageMap } from "@/lib/api";
import { cn } from "@/lib/utils";

export type ChatMessage = EvidenceBundle & {
  id?: number;
  role: "user" | "assistant";
  content: string;
  sources?: string[];
  streaming?: boolean;
  rating?: number | null;
  eval_faithfulness?: number | null;
  eval_relevancy?: number | null;
};

function scoreColor(score: number) {
  if (score >= 0.75) return "text-success border-success/30 bg-success/10";
  if (score >= 0.4) return "text-amber-600 border-amber-500/30 bg-amber-500/10";
  return "text-destructive border-destructive/30 bg-destructive/10";
}

type Props = {
  convId?: number;
  message: ChatMessage;
  onRate?: (messageId: number, rating: -1 | 0 | 1) => void;
  lineage?: LineageMap;
};

export default function MessageBubble({ message, onRate, lineage = {}, convId }: Props) {
  const isUser = message.role === "user";
  const [open, setOpen] = useState(false);

  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-xl px-4 py-3 text-[13px] leading-relaxed",
          isUser
            ? "brand-gradient rounded-br-sm text-white shadow-md shadow-black/10"
            : "rounded-bl-sm border border-slate-200 bg-white text-card-foreground shadow-xs",
        )}
      >
        {!isUser && message.streaming && !message.content && (
          <div className="flex gap-1 py-1">
            <span className="h-2 w-2 rounded-full bg-muted-foreground animate-bounce [animation-delay:-0.3s]" />
            <span className="h-2 w-2 rounded-full bg-muted-foreground animate-bounce [animation-delay:-0.15s]" />
            <span className="h-2 w-2 rounded-full bg-muted-foreground animate-bounce" />
          </div>
        )}

        {message.content && (
          <div className="prose-chat max-w-none">
            {isUser ? (
              <p className="whitespace-pre-wrap m-0">{message.content}</p>
            ) : (
              <>
                <MarkdownContent content={message.content} />
                {message.streaming && (
                  <span className="inline-block w-2 h-4 ml-0.5 bg-foreground/60 animate-pulse align-middle" />
                )}
              </>
            )}
          </div>
        )}

        {!isUser && !message.streaming && (
          <EvidencePanel
            key={`${convId ?? "embed"}:${message.id ?? "new"}:${message.evidence?.map((item) => item.content_sha256).join(",")}`}
            evidence={message.evidence}
            audit={message.citation_audit}
            lineage={lineage}
            checkVersion={
              convId && message.id
                ? async (reference) =>
                    (
                      await api.get(
                        `/conversations/${convId}/messages/${message.id}/evidence/${encodeURIComponent(reference)}`,
                      )
                    ).data.document_status
                : undefined
            }
          />
        )}
        {!isUser &&
          !message.evidence?.length &&
          message.sources &&
          message.sources.length > 0 &&
          !message.streaming && (
            <Collapsible open={open} onOpenChange={setOpen} className="mt-3">
              <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
                {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                Nguồn tài liệu
              </CollapsibleTrigger>
              <CollapsibleContent className="mt-2 flex flex-wrap items-center gap-1.5">
                {message.sources.map((s) => (
                  <span key={s} className="inline-flex items-center gap-1">
                    <Badge variant="outline" className="cursor-default text-xs">
                      {s}
                    </Badge>
                    <LineageBadge lineage={lineage} filename={s} />
                  </span>
                ))}
              </CollapsibleContent>
            </Collapsible>
          )}

        {!isUser && !message.streaming && (message.eval_faithfulness != null || message.eval_relevancy != null) && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {message.eval_faithfulness != null && (
              <span
                title="Faithfulness — mức độ câu trả lời bám sát tài liệu nguồn (chấm tự động bởi AI)"
                className={cn(
                  "rounded-full border px-2 py-0.5 text-[10px] font-medium",
                  scoreColor(message.eval_faithfulness),
                )}
              >
                Bám sát nguồn {Math.round(message.eval_faithfulness * 100)}%
              </span>
            )}
            {message.eval_relevancy != null && (
              <span
                title="Relevancy — mức độ câu trả lời đúng trọng tâm câu hỏi (chấm tự động bởi AI)"
                className={cn(
                  "rounded-full border px-2 py-0.5 text-[10px] font-medium",
                  scoreColor(message.eval_relevancy),
                )}
              >
                Đúng trọng tâm {Math.round(message.eval_relevancy * 100)}%
              </span>
            )}
          </div>
        )}

        {!isUser && message.id && !message.streaming && onRate && (
          <div className="mt-2 flex items-center gap-1">
            <button
              type="button"
              aria-label="Câu trả lời hữu ích"
              onClick={() => onRate(message.id!, message.rating === 1 ? 0 : 1)}
              className={cn(
                "rounded-[0.25rem] p-1 hover:bg-success/10 transition-colors",
                message.rating === 1 && "text-success bg-success/10",
              )}
            >
              <ThumbsUp className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              aria-label="Câu trả lời chưa tốt"
              onClick={() => onRate(message.id!, message.rating === -1 ? 0 : -1)}
              className={cn(
                "rounded-[0.25rem] p-1 hover:bg-destructive/10 transition-colors",
                message.rating === -1 && "text-destructive bg-destructive/10",
              )}
            >
              <ThumbsDown className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
