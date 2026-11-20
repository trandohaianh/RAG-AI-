"use client";

import { Suspense, useCallback, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import MessageBubble, { type ChatMessage } from "@/components/MessageBubble";

/** Origin của trang đang nhúng iframe này ("" khi mở trực tiếp hoặc không xác định được).
 * `ancestorOrigins` do trình duyệt điền nên trang cha không giả được; Firefox không có nó thì
 * dùng `document.referrer` (trang cha chỉ có thể xoá chứ không đổi được origin). */
function embedParentOrigin(): string {
  if (typeof window === "undefined" || window.parent === window) return "";
  const ancestors = (window.location as Location & { ancestorOrigins?: DOMStringList }).ancestorOrigins;
  if (ancestors && ancestors.length > 0) return ancestors[0];
  try {
    return document.referrer ? new URL(document.referrer).origin : "";
  } catch {
    return "";
  }
}

function EmbedChatInner() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  // Bản build đã cấu hình API thì bỏ qua ?api=: nếu không, một link tự chế có thể gửi câu
  // hỏi của khách truy cập tới máy chủ tuỳ ý. Chỉ bản dev (không có biến) mới đọc tham số.
  const apiBase = (process.env.NEXT_PUBLIC_API_URL || params.get("api") || "http://127.0.0.1:8000").replace(/\/$/, "");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const convIdRef = useRef<number | null>(null);
  const sessionTokenRef = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const send = useCallback(async () => {
    const question = input.trim();
    if (!question || loading || !token) return;
    setInput("");
    setLoading(true);
    setMessages((prev) => [
      ...prev,
      { role: "user", content: question },
      { role: "assistant", content: "", streaming: true },
    ]);

    try {
      const res = await fetch(`${apiBase}/embed/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Embed-Token": token,
          // Request đi từ iframe nên Origin luôn là origin của ứng dụng; backend so origin của
          // trang cha (website khách) với allowed_origins của token.
          "X-Embed-Parent-Origin": embedParentOrigin(),
        },
        body: JSON.stringify({
          question,
          conversation_id: convIdRef.current,
          session_token: sessionTokenRef.current,
        }),
      });
      if (!res.ok || !res.body) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "Lỗi chat");
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const data = JSON.parse(line.slice(6));
          if (data.token) {
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === "assistant") {
                next[next.length - 1] = {
                  ...last,
                  content: last.content + data.token,
                  streaming: true,
                };
              }
              return next;
            });
          }
          if (data.done) {
            if (data.conversation_id) convIdRef.current = data.conversation_id;
            if (data.session_token) sessionTokenRef.current = data.session_token;
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === "assistant") {
                next[next.length - 1] = {
                  ...last,
                  sources: data.sources ?? [],
                  evidence: data.evidence ?? [],
                  citation_audit: data.citation_audit ?? null,
                  streaming: false,
                };
              }
              return next;
            });
          }
          if (data.error) throw new Error(data.error);
        }
      }
    } catch (e) {
      setMessages((prev) => {
        const next = [...prev];
        next[next.length - 1] = {
          role: "assistant",
          content: `Lỗi: ${e instanceof Error ? e.message : "unknown"}`,
          streaming: false,
        };
        return next;
      });
    } finally {
      setLoading(false);
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [apiBase, input, loading, token]);

  if (!token) {
    return <div className="p-4 text-sm text-destructive">Thiếu mã nhúng trong đường dẫn.</div>;
  }

  return (
    <div className="flex flex-col h-screen bg-background">
      <header className="border-b px-4 py-2 text-sm font-medium">RAG Chat — Embed</header>
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full min-h-[200px] text-center text-muted-foreground px-4">
            <MessageSquare className="h-10 w-10 mb-3 opacity-40" />
            <p className="text-sm font-medium text-foreground">Widget chat (embed)</p>
            <p className="text-xs mt-2 max-w-sm">
              Giao diện nhỏ để nhúng vào website. Gõ câu hỏi bên dưới — bot trả lời theo tài liệu của tài khoản chủ
              token.
            </p>
            <Button variant="link" size="sm" className="mt-3" asChild>
              <Link href="/chat">Mở ứng dụng đầy đủ (tài liệu, thống kê) →</Link>
            </Button>
          </div>
        )}
        {messages.map((m, i) => (
          <MessageBubble key={i} message={m} />
        ))}
        <div ref={bottomRef} />
      </div>
      <div className="border-t p-3 flex gap-2">
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Hỏi về tài liệu..."
          maxLength={4000}
          rows={2}
          className="resize-none"
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <Button onClick={send} disabled={loading}>
          Gửi
        </Button>
      </div>
    </div>
  );
}

export default function EmbedPage() {
  return (
    <Suspense fallback={<div className="p-4 text-sm">Đang tải...</div>}>
      <EmbedChatInner />
    </Suspense>
  );
}
