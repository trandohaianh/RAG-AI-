"use client";

import ThemeToggle from "./ThemeToggle";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BarChart3,
  FileText,
  LogOut,
  MessageSquarePlus,
  Settings,
  Shield,
  Menu,
  Trash2,
  X,
  ClipboardList,
  Route,
  Plug,
  Wallet,
} from "lucide-react";
import { useEffect, useState } from "react";
import api, { type Conversation, type DocItem } from "@/lib/api";
import { useAuth, type User } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import Logo from "@/components/Logo";
import { cn } from "@/lib/utils";

type Props = {
  mobileOpen: boolean;
  onMobileClose: () => void;
};

export default function Sidebar({ mobileOpen, onMobileClose }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [docs, setDocs] = useState<DocItem[]>([]);

  const load = async () => {
    try {
      const [c, d] = await Promise.all([
        api.get<Conversation[]>("/conversations"),
        api.get<DocItem[]>("/documents"),
      ]);
      setConversations(Array.isArray(c.data) ? c.data : []);
      setDocs(Array.isArray(d.data) ? d.data : []);
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [pathname]);

  async function newChat() {
    const res = await api.post<Conversation>("/conversations", {
      title: "Câu hỏi mới",
    });
    router.push(`/chat/${res.data.id}`);
    onMobileClose();
    load();
  }

  async function deleteConversation(id: number, e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm("Xóa hội thoại này?")) return;
    try {
      await api.delete(`/conversations/${id}`);
      if (pathname === `/chat/${id}`) router.push("/chat");
      load();
    } catch {
      /* ignore */
    }
  }

  const nav = [
    { href: "/field", label: "Ca hiện trường", icon: ClipboardList },
    { href: "/field/schedule", label: "Điều phối tuyến", icon: Route },
    { href: "/field/integrations", label: "Kết nối CRM/NMS", icon: Plug },
    { href: "/docs", label: "Tài liệu", icon: FileText },
    { href: "/analytics", label: "Thống kê", icon: BarChart3 },
    { href: "/settings", label: "Cài đặt", icon: Settings },
    ...(user?.role === "admin"
      ? [
          { href: "/admin/costs", label: "Chi phí", icon: Wallet },
          { href: "/admin", label: "Quản trị", icon: Shield },
        ]
      : []),
  ];
  // Mục khớp dài nhất thắng: đang ở /admin/costs thì chỉ "Chi phí" sáng, không sáng cả "Quản trị".
  const activeHref = nav
    .map((n) => n.href)
    .filter((href) => pathname === href || pathname.startsWith(`${href}/`))
    .sort((a, b) => b.length - a.length)[0];

  const sidebar = (
    <aside className="flex flex-col h-full bg-slate-50 md:bg-transparent w-[240px] shrink-0">
      <div className="flex items-center justify-between px-4 py-5">
        <Link href="/field">
          <Logo size={30} textClassName="text-sm" />
        </Link>
        <button type="button" className="md:hidden" onClick={onMobileClose}>
          <X className="h-5 w-5" />
        </button>
      </div>

      <nav className="px-3 space-y-0.5">
        {nav.map((n) => (
          <Link
            key={n.href}
            href={n.href}
            onClick={onMobileClose}
            className={cn(
              "flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-slate-600 hover:bg-white hover:shadow-xs transition-all",
              activeHref === n.href && "bg-white shadow-xs font-medium text-slate-900"
            )}
          >
            <n.icon className={cn("h-4 w-4", activeHref === n.href && "text-primary")} />
            {n.label}
          </Link>
        ))}
      </nav>

      <div className="px-3 mt-4">
        <Button
          className="w-full justify-start gap-2 brand-gradient border-0 text-white shadow-md shadow-black/10 hover:opacity-90 hover:shadow-lg hover:shadow-black/20 transition-all rounded-full h-10"
          onClick={newChat}
        >
          <MessageSquarePlus className="h-4 w-4" />
          Hỏi trợ lý
        </Button>
      </div>

      <ScrollArea className="flex-1 px-3 mt-5">
        {conversations.length > 0 && (
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 px-2 py-1">Hội thoại gần đây</p>
        )}
        <ul className="space-y-0.5 pb-4">
          {conversations.map((c) => (
            <li key={c.id} className="group flex items-center gap-0.5">
              <Link
                href={`/chat/${c.id}`}
                onClick={onMobileClose}
                className={cn(
                  "flex-1 min-w-0 rounded-lg px-2.5 py-1.5 text-sm truncate text-slate-600 hover:bg-white hover:shadow-xs transition-all",
                  pathname === `/chat/${c.id}` &&
                    "bg-white shadow-xs font-medium text-slate-900"
                )}
              >
                {c.title}
              </Link>
              <button
                type="button"
                onClick={(e) => deleteConversation(c.id, e)}
                className="shrink-0 p-1 rounded-[0.25rem] opacity-0 group-hover:opacity-100 hover:bg-destructive/10 text-destructive"
                title="Xóa hội thoại"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>

        {docs.length > 0 && (
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 px-2 py-1">Tài liệu của đội</p>
        )}
        <ul className="space-y-0.5 pb-3">
          {docs.slice(0, 8).map((d) => (
            <li key={d.name} className="flex items-center gap-1 px-2.5 py-1.5 text-xs text-slate-500">
              <span className="truncate flex-1">{d.name}</span>
              <Badge variant="outline" className="text-[10px] px-1.5 rounded-full border-slate-200">
                {d.chunks}
              </Badge>
            </li>
          ))}
        </ul>
      </ScrollArea>

      <div className="p-3">
        <div className="pt-2 border-t border-slate-200">
          <UserFooter user={user} onLogout={logout} />
        </div>
      </div>
    </aside>
  );

  return (
    <>
      <div className="hidden md:flex shrink-0">{sidebar}</div>
      {mobileOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={onMobileClose} />
          <div className="absolute left-0 top-0 h-full z-10">{sidebar}</div>
        </div>
      )}
    </>
  );
}

export function MobileMenuButton({ onOpen }: { onOpen: () => void }) {
  return (
    <Button variant="ghost" size="icon" className="md:hidden" onClick={onOpen}>
      <Menu className="h-5 w-5" />
    </Button>
  );
}

function UserFooter({ user, onLogout }: { user: User | null; onLogout: () => void }) {
  if (!user) return null;
  return (
    <div className="flex items-center gap-2 pt-2">
      <div className="brand-gradient h-8 w-8 rounded-full flex items-center justify-center text-xs font-bold text-white shadow-xs shadow-black/20">
        {user.email[0].toUpperCase()}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium truncate">{user.email}</p>
        <Badge variant="secondary" className="text-[10px] mt-0.5">
          {user.role}
        </Badge>
      </div>
      <ThemeToggle compact />
      <Button variant="ghost" size="icon" className="h-8 w-8" onClick={onLogout} title="Đăng xuất">
        <LogOut className="h-4 w-4" />
      </Button>
    </div>
  );
}
