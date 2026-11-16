"use client";

import Link from "next/link";
import { ArrowLeft, Sparkle } from "lucide-react";
import { cn } from "@/lib/utils";

const SPARKLES = [
  { top: "8%", left: "18%", size: 14, delay: "0s", opacity: 0.5 },
  { top: "22%", left: "82%", size: 10, delay: "0.4s", opacity: 0.35 },
  { top: "68%", left: "10%", size: 12, delay: "0.8s", opacity: 0.4 },
  { top: "78%", left: "88%", size: 16, delay: "0.2s", opacity: 0.55 },
  { top: "45%", left: "92%", size: 8, delay: "1.1s", opacity: 0.3 },
  { top: "88%", left: "40%", size: 10, delay: "0.6s", opacity: 0.35 },
];

export default function AuthBackdrop({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative min-h-screen overflow-hidden bg-[#08070c] text-white">
      {/* diagonal ribbon glows */}
      <div className="pointer-events-none absolute -top-40 -right-40 h-[520px] w-[520px] rotate-[18deg] rounded-[40%] bg-gradient-to-br from-white/10 via-white/[0.03] to-transparent blur-2xl" />
      <div className="pointer-events-none absolute -bottom-52 -left-40 h-[480px] w-[480px] rotate-[-12deg] rounded-[40%] bg-gradient-to-tr from-white/[0.06] via-white/[0.02] to-transparent blur-2xl" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,_transparent_0%,_#08070c_78%)]" />

      {SPARKLES.map((s, i) => (
        <Sparkle
          key={i}
          className="pointer-events-none absolute animate-pulse text-amber-300"
          style={{ top: s.top, left: s.left, width: s.size, height: s.size, opacity: s.opacity, animationDelay: s.delay, animationDuration: "3s" }}
          fill="currentColor"
        />
      ))}

      <Link
        href="/"
        className="absolute left-6 top-6 z-20 flex items-center gap-1.5 text-sm font-medium text-white/70 transition-colors hover:text-white"
      >
        <ArrowLeft className="h-4 w-4" />
        Trang chủ
      </Link>

      <div className="relative z-10 flex min-h-screen items-center justify-center p-6">
        {children}
      </div>
    </div>
  );
}

export function darkInputClass(extra?: string) {
  return cn(
    "w-full rounded-full border border-white/10 bg-white/[0.04] px-5 py-3 text-sm text-white placeholder:text-white/30",
    "outline-hidden transition-colors focus:border-white/25 focus:bg-white/[0.06]",
    extra
  );
}
