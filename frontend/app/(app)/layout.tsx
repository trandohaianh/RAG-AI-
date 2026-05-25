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
    { href: "/chat", label: "Chat" },
    { href: "/docs", label: "Tài liệu" },
    { href: "/analytics", label: "Thống kê" },
    { href: "/settings", label: "Cài đặt" },
    ...(user.role === "admin" ? [{ href: "/admin", label: "Admin" }] : []),
  ];

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar mobileOpen={mobileOpen} onMobileClose={() => setMobileOpen(false)} />
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-12 border-b border-border flex items-center gap-4 px-4 shrink-0">
          <MobileMenuButton onOpen={() => setMobileOpen(true)} />
          <nav className="flex gap-1">
            {tabs.map((t) => (
              <Link
                key={t.href}
                href={t.href}
                className={cn(
                  "px-3 py-1.5 rounded-md text-sm hover:bg-accent",
                  pathname.startsWith(t.href) && "bg-accent font-medium"
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
