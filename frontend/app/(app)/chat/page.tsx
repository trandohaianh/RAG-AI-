"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { MessageSquarePlus, FileText } from "lucide-react";
import api, { type Conversation } from "@/lib/api";
import { Button } from "@/components/ui/button";

/** Mở hội thoại gần nhất hoặc màn hình bắt đầu — không tự tạo mới mỗi lần vào /chat */
export default function ChatIndexPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (!cancelled) setLoading(false);
    }, 4000);

    api
      .get<Conversation[]>("/conversations", { timeout: 3000 })
      .then((res) => {
        if (cancelled) return;
        const list = Array.isArray(res.data) ? res.data : [];
        if (list.length > 0) {
          router.replace(`/chat/${list[0].id}`);
        } else {
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      })
      .finally(() => window.clearTimeout(timer));

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [router]);

  async function startChat() {
    const res = await api.post<Conversation>("/conversations", {
      title: "Cuộc trò chuyện mới",
    });
    router.push(`/chat/${res.data.id}`);
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground text-sm">
        Đang tải...
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 p-6 text-center max-w-md mx-auto">
      <MessageSquarePlus className="h-12 w-12 text-primary" />
      <div>
        <h2 className="text-lg font-semibold mb-2">Bắt đầu hỏi đáp</h2>
        <p className="text-sm text-muted-foreground">
          Upload tài liệu PDF, DOCX hoặc TXT trước, sau đó tạo cuộc trò chuyện để hỏi về nội dung.
        </p>
      </div>
      <div className="flex flex-col sm:flex-row gap-2 w-full">
        <Button asChild variant="outline" className="flex-1">
          <Link href="/docs">
            <FileText className="h-4 w-4 mr-2" />
            Tài liệu
          </Link>
        </Button>
        <Button className="flex-1" onClick={startChat}>
          Tạo hội thoại
        </Button>
      </div>
    </div>
  );
}
