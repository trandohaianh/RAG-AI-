"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

const ORDER = ["light", "dark", "system"] as const;
const LABEL: Record<string, string> = { light: "Sáng", dark: "Tối", system: "Theo máy" };

/** Sáng → Tối → Theo máy. next-themes lưu lựa chọn và chèn script chống nháy màu khi tải trang. */
export default function ThemeToggle({ compact = false, className = "" }: { compact?: boolean; className?: string }) {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const current = mounted && theme ? theme : "system";
  const next = ORDER[(ORDER.indexOf(current as (typeof ORDER)[number]) + 1) % ORDER.length];
  const Icon = current === "light" ? Sun : current === "dark" ? Moon : Monitor;
  return (
    <button type="button" onClick={() => setTheme(next)} title={`Giao diện: ${LABEL[current]}`}
      aria-label={`Giao diện: ${LABEL[current]}. Bấm để chuyển sang ${LABEL[next]}`}
      className={`inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:text-slate-900 ${className}`}>
      <Icon className="h-3.5 w-3.5" />{!compact && <span>{LABEL[current]}</span>}
    </button>
  );
}
