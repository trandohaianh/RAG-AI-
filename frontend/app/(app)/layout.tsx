"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import Sidebar, { MobileMenuButton } from "@/components/Sidebar";
import VerifyEmailBanner from "@/components/VerifyEmailBanner";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (mounted && !loading && !user) router.replace("/login");
  }, [mounted, loading, user, router]);

  if (!mounted || loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-muted-foreground text-sm">Đang tải...</div>
    );
  }

  if (!user) {
    return (
      <div className="min-h-screen flex items-center justify-center text-muted-foreground text-sm">
        Chuyển đến đăng nhập...
      </div>
    );
  }

  // Điều hướng chỉ còn một chỗ duy nhất: thanh bên. Trước đây thanh bên và
  // thanh trên lặp lại y hệt nhau, nên người dùng phải tự đoán bấm cái nào —
  // và trên màn hình hẹp thì hai hàng điều hướng chiếm mất chỗ của nội dung.
  // Thanh trên giờ chỉ trả lời một câu: đang ở mục nào.
  const SECTIONS: { href: string; label: string }[] = [
    { href: "/field", label: "Ca hiện trường" },
    { href: "/chat", label: "Trợ lý tài liệu" },
    { href: "/docs", label: "Tài liệu cá nhân" },
    { href: "/analytics", label: "Thống kê" },
    { href: "/settings", label: "Cài đặt" },
    { href: "/admin/costs", label: "Chi phí AI & hạ tầng" },
    { href: "/admin", label: "Quản trị" },
  ];
  const section = SECTIONS.find((s) => pathname.startsWith(s.href))?.label ?? "";

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50 p-0 md:p-3 md:gap-3">
      <Sidebar mobileOpen={mobileOpen} onMobileClose={() => setMobileOpen(false)} />
      <div className="flex-1 flex flex-col min-w-0 bg-white md:rounded-xl md:border md:border-slate-200 md:shadow-xs overflow-hidden">
        <header className="h-12 border-b border-border flex items-center gap-3 px-4 shrink-0">
          <MobileMenuButton onOpen={() => setMobileOpen(true)} />
          <span className="text-[13px] font-semibold tracking-tight text-slate-900">{section}</span>
          <span className="text-[13px] text-slate-400">FieldOps</span>
        </header>
        <VerifyEmailBanner />
        <main className="flex-1 min-h-0 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
