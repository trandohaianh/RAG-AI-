"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import ChatWindow from "@/components/ChatWindow";
import api, { type Message } from "@/lib/api";
import type { ChatMessage } from "@/components/MessageBubble";

export default function ChatDetailPage() {
  const params = useParams();
  const convId = Number(params.id);
  const [initial, setInitial] = useState<ChatMessage[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!convId || Number.isNaN(convId)) return;
    api
      .get<Message[]>(`/conversations/${convId}/messages`)
      .then((res) => {
        setInitial(
          res.data.map((m) => ({
            id: m.id,
            role: m.role as "user" | "assistant",
            content: m.content,
            sources: m.sources,
            evidence: m.evidence,
            citation_audit: m.citation_audit,
            rating: m.rating,
            eval_faithfulness: m.eval_faithfulness,
            eval_relevancy: m.eval_relevancy,
          })),
        );
      })
      .catch(() => setInitial([]))
      .finally(() => setReady(true));
  }, [convId]);

  if (!ready) {
    return <div className="flex h-full items-center justify-center text-muted-foreground">Đang tải hội thoại...</div>;
  }

  return (
    <div className="h-full p-2 md:p-4">
      <ChatWindow convId={convId} initialMessages={initial} />
    </div>
  );
}
