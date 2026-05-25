"use client";

import { useState } from "react";
import MarkdownContent from "@/components/MarkdownContent";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  sources?: string[];
  streaming?: boolean;
};

export default function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const [open, setOpen] = useState(false);

  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-4 py-3 text-sm shadow-sm",
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-secondary text-secondary-foreground"
        )}
      >
        {!isUser && message.streaming && !message.content && (
          <div className="flex gap-1 py-1">
            <span className="h-2 w-2 rounded-full bg-muted-foreground animate-bounce [animation-delay:-0.3s]" />
            <span className="h-2 w-2 rounded-full bg-muted-foreground animate-bounce [animation-delay:-0.15s]" />
            <span className="h-2 w-2 rounded-full bg-muted-foreground animate-bounce" />
          </div>
        )}

        {message.content && (
          <div className="prose-chat max-w-none">
            {isUser ? (
              <p className="whitespace-pre-wrap m-0">{message.content}</p>
            ) : (
              <>
                <MarkdownContent content={message.content} />
                {message.streaming && (
                  <span className="inline-block w-2 h-4 ml-0.5 bg-foreground/60 animate-pulse align-middle" />
                )}
              </>
            )}
          </div>
        )}

        {!isUser && message.sources && message.sources.length > 0 && !message.streaming && (
          <Collapsible open={open} onOpenChange={setOpen} className="mt-3">
            <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
              {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              Nguồn tài liệu
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-2 flex flex-wrap gap-1.5">
              {message.sources.map((s) => (
                <Badge key={s} variant="outline" className="cursor-default text-xs">
                  {s}
                </Badge>
              ))}
            </CollapsibleContent>
          </Collapsible>
        )}
      </div>
    </div>
  );
}
