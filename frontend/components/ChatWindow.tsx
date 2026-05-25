"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import MessageBubble, { type ChatMessage } from "@/components/MessageBubble";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import SuggestionChips from "@/components/SuggestionChips";
import { streamChat } from "@/lib/api";

type Props = {
  convId: number;
  initialMessages?: ChatMessage[];
};

export default function ChatWindow({ convId, initialMessages = [] }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMessages(initialMessages);
  }, [initialMessages, convId]);

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
        onDone: (sources) => {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = { ...last, sources, streaming: false };
            }
            return next;
          });
          setLoading(false);
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
      });
    },
    [convId, loading]
  );

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  const empty = messages.length === 0;

  return (
    <div className="flex flex-1 flex-col min-h-0 h-full">
      <ScrollArea className="flex-1 px-1 min-h-0">
        <div className="max-w-3xl mx-auto space-y-4 py-4">
          {empty && (
            <div className="text-center py-12 space-y-4">
              <h2 className="text-xl font-semibold">Xin chào! Tôi có thể giúp gì?</h2>
              <p className="text-muted-foreground text-sm">
                Trả lời theo tài liệu đã upload (PDF, DOCX, TXT, ảnh OCR)
              </p>
              <p className="text-xs text-primary font-medium">
                ↓ Cuộn xuống — gợi ý câu hỏi nằm ngay trên ô nhập chat
              </p>
            </div>
          )}
          {messages.map((m, i) => (
            <MessageBubble key={i} message={m} />
          ))}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>

      <div className="max-w-3xl mx-auto w-full shrink-0 border-t border-border">
        <SuggestionChips onPick={send} disabled={loading} />
        <div className="p-4 flex gap-2 items-end">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Nhập câu hỏi... (Enter gửi)"
            disabled={loading}
            rows={2}
            className="resize-none min-h-[44px]"
          />
          <Button onClick={() => send(input)} disabled={loading || !input.trim()} size="icon">
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

