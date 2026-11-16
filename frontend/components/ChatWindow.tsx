"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, FileText, Send } from "lucide-react";
import MessageBubble, { type ChatMessage } from "@/components/MessageBubble";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import SuggestionChips from "@/components/SuggestionChips";
import api, {
  getDocLineage,
  sendFeedback,
  streamChat,
  type DocItem,
  type LineageMap,
  type Message,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  convId: number;
  initialMessages?: ChatMessage[];
};

export default function ChatWindow({ convId, initialMessages = [] }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [lineage, setLineage] = useState<LineageMap>({});
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMessages(initialMessages);
  }, [initialMessages, convId]);

  useEffect(() => {
    getDocLineage()
      .then(setLineage)
      .catch(() => setLineage({}));
    api
      .get<DocItem[]>("/documents")
      .then((res) => setDocs(Array.isArray(res.data) ? res.data : []))
      .catch(() => setDocs([]));
  }, []);

  useEffect(() => {
    // Nếu tài liệu đang chọn bị xoá, quay về "Tất cả tài liệu" thay vì lọc theo tên không còn tồn tại.
    if (selectedSource && docs.length > 0 && !docs.some((d) => d.name === selectedSource)) {
      setSelectedSource(null);
    }
  }, [docs, selectedSource]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const send = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || loading) return;
      setInput("");
      setLoading(true);
      setMessages((prev) => [
        ...prev,
        { role: "user", content: question },
        { role: "assistant", content: "", streaming: true },
      ]);

      await streamChat(convId, question, {
        onToken: (token) => {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = { ...last, content: last.content + token, streaming: true };
            }
            return next;
          });
        },
        onDone: (sources, messageId, evidence, citation_audit) => {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = {
                ...last,
                sources, evidence, citation_audit,
                streaming: false,
                id: messageId ?? undefined,
                rating: null,
              };
            }
            return next;
          });
          setLoading(false);
          if (messageId) pollEvalScore(messageId);
        },
        onError: (err) => {
          setMessages((prev) => {
            const next = [...prev];
            next[next.length - 1] = {
              role: "assistant",
              content: `Lỗi: ${err}`,
              streaming: false,
            };
            return next;
          });
          setLoading(false);
        },
      }, selectedSource);
    },
    [convId, loading, selectedSource]
  );

  const pollEvalScore = useCallback(
    async (messageId: number) => {
      for (let attempt = 0; attempt < 4; attempt++) {
        await new Promise((r) => setTimeout(r, 2500));
        try {
          const res = await api.get<Message[]>(`/conversations/${convId}/messages`);
          const found = res.data.find((m) => m.id === messageId);
          if (found && found.eval_faithfulness != null) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === messageId
                  ? {
                      ...m,
                      eval_faithfulness: found.eval_faithfulness,
                      eval_relevancy: found.eval_relevancy,
                    }
                  : m
              )
            );
            return;
          }
        } catch {
          /* bỏ qua — không quan trọng bằng luồng chat chính */
        }
      }
    },
    [convId]
  );

  const handleRate = useCallback(
    async (messageId: number, rating: -1 | 0 | 1) => {
      setMessages((prev) =>
        prev.map((m) => (m.id === messageId ? { ...m, rating } : m))
      );
      try {
        await sendFeedback(convId, messageId, rating);
      } catch {
        /* bỏ qua lỗi feedback — không chặn luồng chat */
      }
    },
    [convId]
  );

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  const empty = messages.length === 0;

  return (
    <div className="flex flex-1 flex-col min-h-0 h-full bg-slate-50/50">
      <ScrollArea className="flex-1 px-4 min-h-0">
        <div className="max-w-3xl mx-auto space-y-4 py-5">
          {empty && (
            <div className="relative overflow-hidden rounded-xl border border-slate-100 bg-white px-6 py-14 text-center shadow-xs">
              <div
                className="pointer-events-none absolute inset-0 opacity-70"
                style={{
                  backgroundImage: "radial-gradient(circle, #e2e8f0 1px, transparent 1px)",
                  backgroundSize: "20px 20px",
                  maskImage: "radial-gradient(ellipse 60% 60% at 50% 40%, black 30%, transparent 100%)",
                }}
              />
              <div className="relative">
                <div className="brand-gradient mx-auto flex h-14 w-14 items-center justify-center rounded-xl shadow-lg shadow-black/10">
                  <Send className="h-6 w-6 text-white" />
                </div>
                <h2 className="mt-4 text-xl font-semibold tracking-tight text-slate-900">
                  Xin chào! Tôi có thể giúp gì?
                </h2>
                <p className="mt-1.5 text-[13px] text-slate-500">
                  Trả lời theo tài liệu đã tải lên (PDF, DOCX, TXT, ảnh)
                </p>
                <p className="mt-3.5 inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-3 py-1 text-[11px] font-medium text-primary">
                  ↓ Gợi ý câu hỏi nằm ngay trên ô nhập chat
                </p>
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <MessageBubble convId={convId} key={i} message={m} onRate={handleRate} lineage={lineage} />
          ))}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>

      <div className="max-w-3xl mx-auto w-full shrink-0 px-4 pb-4">
        <div className="relative mb-2">
          <button
            type="button"
            onClick={() => setPickerOpen((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[12px] font-medium text-slate-600 shadow-xs hover:bg-slate-50"
          >
            <FileText className="h-3.5 w-3.5 text-primary" />
            Đang hỏi: {selectedSource ?? "Tất cả tài liệu"}
            <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
          </button>

          {pickerOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setPickerOpen(false)} />
              <div className="absolute bottom-full z-20 mb-1.5 w-72 max-h-64 overflow-y-auto rounded-xl border border-slate-200 bg-white p-1.5 shadow-lg">
                <button
                  type="button"
                  onClick={() => {
                    setSelectedSource(null);
                    setPickerOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] hover:bg-slate-50",
                    selectedSource === null && "bg-primary/5 font-medium text-primary"
                  )}
                >
                  Tất cả tài liệu
                </button>
                {docs.map((d) => (
                  <button
                    key={d.name}
                    type="button"
                    onClick={() => {
                      setSelectedSource(d.name);
                      setPickerOpen(false);
                    }}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] hover:bg-slate-50",
                      selectedSource === d.name && "bg-primary/5 font-medium text-primary"
                    )}
                  >
                    <span className="truncate">{d.name}</span>
                    <span className="shrink-0 text-[11px] text-slate-400">{d.chunks} chunks</span>
                  </button>
                ))}
                {docs.length === 0 && (
                  <p className="px-2.5 py-2 text-[12px] text-slate-400">Chưa có tài liệu — tải lên ở mục Tài liệu</p>
                )}
              </div>
            </>
          )}
        </div>

        <SuggestionChips onPick={send} disabled={loading} />
        <div className="flex items-end gap-2 rounded-xl border border-slate-200 bg-white p-1.5 shadow-xs">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Nhập câu hỏi... (Enter gửi)"
            disabled={loading}
            rows={1}
            className="min-h-[38px] resize-none border-0 shadow-none text-[13px] focus-visible:ring-0"
          />
          <Button
            onClick={() => send(input)}
            disabled={loading || !input.trim()}
            size="icon"
            className="brand-gradient shrink-0 rounded-full border-0 shadow-md shadow-black/20"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

