"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BarChart3,
  Brain,
  FileText,
  LogOut,
  MessageSquarePlus,
  Settings,
  Shield,
  Menu,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import api, { type Conversation, type DocItem } from "@/lib/api";
import { useAuth, type User } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
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
      title: "Cuộc trò chuyện mới",
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
    { href: "/docs", label: "Tài liệu", icon: FileText },
    { href: "/analytics", label: "Thống kê", icon: BarChart3 },
    { href: "/settings", label: "Cài đặt", icon: Settings },
    ...(user?.role === "admin"
      ? [{ href: "/admin", label: "Admin", icon: Shield }]
      : []),
  ];

  const sidebar = (
    <aside className="flex flex-col h-full bg-sidebar border-r border-border w-[220px]">
      <div className="p-4 border-b border-border flex items-center justify-between">
        <Link href="/chat" className="flex items-center gap-2 font-semibold text-primary">
          <Brain className="h-5 w-5" />
          RAG Chat
        </Link>
        <button type="button" className="md:hidden" onClick={onMobileClose}>
          <X className="h-5 w-5" />
        </button>
      </div>

      <div className="p-3">
        <Button className="w-full justify-start gap-2" onClick={newChat}>
          <MessageSquarePlus className="h-4 w-4" />
          Chat mới
        </Button>
      </div>

      <ScrollArea className="flex-1 px-2">
        <p className="text-xs font-medium text-muted-foreground px-2 py-1">Hội thoại</p>
        <ul className="space-y-0.5 pb-3">
          {conversations.length === 0 && (
            <li className="px-2 py-1 text-xs text-muted-foreground">Chưa có hội thoại</li>
          )}
          {conversations.map((c) => (
            <li key={c.id} className="group flex items-center gap-0.5">
              <Link
                href={`/chat/${c.id}`}
                onClick={onMobileClose}
                className={cn(
                  "flex-1 min-w-0 rounded-md px-2 py-1.5 text-sm truncate hover:bg-accent",
                  pathname === `/chat/${c.id}` && "bg-accent font-medium"
                )}
              >
                {c.title}
              </Link>
              <button
                type="button"
                onClick={(e) => deleteConversation(c.id, e)}
                className="shrink-0 p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-destructive/10 text-destructive"
                title="Xóa hội thoại"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>

        <p className="text-xs font-medium text-muted-foreground px-2 py-1">Tài liệu</p>
        <ul className="space-y-0.5 pb-3">
          {docs.slice(0, 8).map((d) => (
            <li key={d.name} className="flex items-center gap-1 px-2 py-1 text-xs text-muted-foreground">
              <span className="truncate flex-1">{d.name}</span>
              <Badge variant="outline" className="text-[10px] px-1">
                {d.chunks}
              </Badge>
            </li>
          ))}
        </ul>
      </ScrollArea>

      <div className="p-3 border-t border-border space-y-2">
        {nav.map((n) => (
          <Link
            key={n.href}
            href={n.href}
            onClick={onMobileClose}
            className={cn(
              "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent",
              pathname.startsWith(n.href) && "bg-accent"
            )}
          >
            <n.icon className="h-4 w-4" />
            {n.label}
          </Link>
        ))}
        <UserFooter user={user} onLogout={logout} />
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
      <div className="h-8 w-8 rounded-full bg-primary/20 flex items-center justify-center text-xs font-bold text-primary">
        {user.email[0].toUpperCase()}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium truncate">{user.email}</p>
        <Badge variant="secondary" className="text-[10px] mt-0.5">
          {user.role}
        </Badge>
      </div>
      <Button variant="ghost" size="icon" className="h-8 w-8" onClick={onLogout} title="Đăng xuất">
        <LogOut className="h-4 w-4" />
      </Button>
    </div>
  );
}
