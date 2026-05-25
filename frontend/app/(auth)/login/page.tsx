"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import api, { getApiErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function LoginPage() {
  const router = useRouter();
  const { login, user, loading: authLoading } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!authLoading && user) router.replace("/chat");
  }, [authLoading, user, router]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const res = await api.post("/auth/login", { email, password });
      login(res.data.access_token, res.data.user);
      router.replace("/chat");
    } catch (err: unknown) {
      setError(getApiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-4 bg-muted/30 gap-4">
      <div className="w-full max-w-md rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm">
        <strong>RAG Chat</strong> ≠ Grafana. Nếu thấy logo Grafana → mở nhầm cổng Docker.
        Chạy <code className="text-xs">cd frontend && npm run dev</code> rồi mở URL Next.js (thường{" "}
        <code className="text-xs">localhost:3001</code> — hiện cổng 3000 đang là Grafana/Docker).
      </div>
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Đăng nhập RAG Chat</CardTitle>
          <p className="text-sm text-muted-foreground">
            Chưa có tài khoản?{" "}
            <Link href="/register" className="text-primary font-medium hover:underline">
              Đăng ký ngay
            </Link>
            {" · "}
            <Link href="/" className="hover:underline">
              Trang chủ
            </Link>
          </p>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            {error && <p className="text-sm text-destructive">{error}</p>}
            <div>
              <label className="text-sm font-medium">Email</label>
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-sm font-medium">Mật khẩu</label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="mt-1"
              />
            </div>
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Đang đăng nhập..." : "Đăng nhập"}
            </Button>
          </form>
          <Button type="button" variant="outline" className="w-full mt-4" asChild>
            <Link href="/register">Tạo tài khoản mới</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
