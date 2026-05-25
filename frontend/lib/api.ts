import axios from "axios";

function resolveApiUrl(): string {
  const raw = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  return `https://${raw}`;
}

const API_URL = resolveApiUrl();

const api = axios.create({
  baseURL: API_URL,
  headers: { "Content-Type": "application/json" },
  timeout: 15000,
});

/** Thông báo lỗi dễ hiểu khi không kết nối được backend */
export function getApiErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    if (err.code === "ERR_NETWORK" || err.message === "Network Error") {
      return `Không kết nối được API (${API_URL}). Backend có đang chạy? Mở http://127.0.0.1:8000/health — nếu lỗi: cd backend ; .\\start.ps1 (giữ cửa sổ PowerShell mở).`;
    }
    const detail = err.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg ?? d).join(", ");
  }
  return err instanceof Error ? err.message : "Lỗi không xác định";
}

export function setAuthToken(token: string | null) {
  if (token) {
    api.defaults.headers.common.Authorization = `Bearer ${token}`;
  } else {
    delete api.defaults.headers.common.Authorization;
  }
}

// Khôi phục token khi import (client)
if (typeof window !== "undefined") {
  const t = localStorage.getItem("rag_token");
  if (t) setAuthToken(t);
}

export default api;

export type Conversation = { id: number; title: string; created_at: string };
export type Message = {
  id: number;
  role: string;
  content: string;
  sources: string[];
  created_at: string;
};
export type DocItem = { name: string; chunks: number; uploaded_at: string | null };
export type SuggestedQuestionsResponse = { questions: string[] };

export async function streamChat(
  convId: number,
  question: string,
  callbacks: {
    onToken: (t: string) => void;
    onDone: (sources: string[]) => void;
    onError: (e: string) => void;
  }
) {
  const token = localStorage.getItem("rag_token");
  const res = await fetch(`${API_URL}/chat/${convId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ question }),
  });

  if (!res.ok || !res.body) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    callbacks.onError(err.detail ?? "Lỗi chat");
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(line.slice(6));
        if (data.error) {
          callbacks.onError(data.error);
          return;
        }
        if (data.token) callbacks.onToken(data.token);
        if (data.done) callbacks.onDone(data.sources ?? []);
      } catch {
        /* skip */
      }
    }
  }
}
