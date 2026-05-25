"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Brain, FileText, MessageSquare, BarChart3, Settings } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";

export default function HomePage() {
  const router = useRouter();
  const { user, loading } = useAuth();

  useEffect(() => {
    if (!loading && user) router.replace("/chat");
  }, [loading, user, router]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-muted-foreground">
        Đang tải...
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-background to-muted/40">
      <header className="max-w-5xl mx-auto px-6 py-6 flex items-center justify-between">
        <div className="flex items-center gap-2 font-semibold text-primary text-lg">
          <Brain className="h-6 w-6" />
          RAG Chat
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" asChild>
            <Link href="/login">Đăng nhập</Link>
          </Button>
          <Button asChild>
            <Link href="/register">Đăng ký miễn phí</Link>
          </Button>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-12 space-y-12">
        <section className="text-center space-y-4">
          <h1 className="text-3xl md:text-4xl font-bold tracking-tight">
            Hỏi đáp tài liệu bằng AI
          </h1>
          <p className="text-muted-foreground max-w-2xl mx-auto">
            Upload PDF, DOCX, TXT hoặc ảnh (OCR) — chat RAG với Gemini, analytics, embed widget
            và Public API.
          </p>
          <div className="flex flex-wrap justify-center gap-4 pt-4">
            <Button size="lg" className="min-w-[200px] rounded-lg" asChild>
              <Link href="/register">Bắt đầu — Đăng ký</Link>
            </Button>
            <Button size="lg" variant="outline" className="min-w-[200px] rounded-lg" asChild>
              <Link href="/login">Đã có tài khoản</Link>
            </Button>
          </div>
        </section>

        <section className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {[
            { icon: FileText, title: "Tài liệu", desc: "Upload & lập chỉ mục vector" },
            { icon: MessageSquare, title: "Chat RAG", desc: "Hỏi theo nội dung file của bạn" },
            { icon: BarChart3, title: "Analytics", desc: "Thống kê truy vấn & usage" },
            { icon: Settings, title: "SaaS", desc: "API key, embed, Stripe Pro" },
          ].map((f) => (
            <div key={f.title} className="rounded-xl border bg-card p-5 shadow-sm">
              <f.icon className="h-8 w-8 text-primary mb-3" />
              <h3 className="font-medium">{f.title}</h3>
              <p className="text-sm text-muted-foreground mt-1">{f.desc}</p>
            </div>
          ))}
        </section>

        <p className="text-center text-xs text-muted-foreground">
          User đăng ký đầu tiên sẽ là <strong>admin</strong>. Cần backend chạy tại{" "}
          <code className="text-foreground">localhost:8000</code>
        </p>
      </main>
    </div>
  );
}
