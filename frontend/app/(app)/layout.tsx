"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import Sidebar, { MobileMenuButton } from "@/components/Sidebar";
import Link from "next/link";
import { cn } from "@/lib/utils";

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
      <div className="min-h-screen flex items-center justify-center text-muted-foreground text-sm">
        Đang tải...
      </div>
    );
  }

  if (!user) {
    return (
      <div className="min-h-screen flex items-center justify-center text-muted-foreground text-sm">
        Chuyển đến đăng nhập...
      </div>
    );
  }

  const tabs = [
    { href: "/field", label: "Ca hiện trường" },
    { href: "/chat", label: "Chat" },
    { href: "/docs", label: "Tài liệu" },
    { href: "/analytics", label: "Thống kê" },
    { href: "/settings", label: "Cài đặt" },
    ...(user.role === "admin" ? [{ href: "/admin", label: "Quản trị" }] : []),
  ];

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50 p-0 md:p-3 md:gap-3">
      <Sidebar mobileOpen={mobileOpen} onMobileClose={() => setMobileOpen(false)} />
      <div className="flex-1 flex flex-col min-w-0 bg-white md:rounded-xl md:border md:border-slate-200 md:shadow-sm overflow-hidden">
        <header className="h-12 border-b border-border flex items-center gap-4 px-4 shrink-0">
          <MobileMenuButton onOpen={() => setMobileOpen(true)} />
          <nav className="flex gap-1 rounded-full bg-slate-100 p-1">
            {tabs.map((t) => (
              <Link
                key={t.href}
                href={t.href}
                className={cn(
                  "px-3 py-1 rounded-full text-[13px] font-medium text-slate-500 transition-colors hover:text-slate-900",
                  pathname.startsWith(t.href) && "bg-white text-slate-900 shadow-sm"
                )}
              >
                {t.label}
              </Link>
            ))}
          </nav>
        </header>
        <main className="flex-1 min-h-0 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
