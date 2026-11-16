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

function expireSession(token: string | null) {
  if (typeof window === "undefined" || !token || localStorage.getItem("rag_token") !== token) return;
  localStorage.removeItem("rag_token");
  localStorage.removeItem("rag_user");
  setAuthToken(null);
  if (!window.location.pathname.startsWith("/login")) window.location.href = "/login";
}

// Token hết hạn/không hợp lệ (401) trên API cần đăng nhập → tự đăng xuất thay vì
// hiện lỗi "token không hợp lệ" mãi trong khi UI vẫn tưởng đang đăng nhập.
api.interceptors.response.use(
  (res) => res,
  (err) => {
    const status = err.response?.status;
    const url: string = err.config?.url ?? "";
    const isAuthEndpoint = url.includes("/auth/login") || url.includes("/auth/register");
    const currentToken = typeof window !== "undefined" ? localStorage.getItem("rag_token") : null;
    const sentAuthorization = err.config?.headers?.Authorization ?? err.config?.headers?.authorization;
    if (status === 401 && !isAuthEndpoint && currentToken &&
        sentAuthorization === `Bearer ${currentToken}` && typeof window !== "undefined") {
      expireSession(currentToken);
    }
    return Promise.reject(err);
  }
);

export default api;

export type Evidence = { reference_id: string; chunk_id: string | null; source: string; page: number | null;
  document_version: string | null; collection_revision: string | null; content_sha256: string; quote: string };
export type CitationAudit = { status: string; cited_references: string[]; invalid_references: string[]; semantic_support_verified: boolean };
export type EvidenceBundle = { evidence?: Evidence[]; citation_audit?: CitationAudit | null };
export type Conversation = { id: number; title: string; created_at: string };
export type Message = EvidenceBundle & {
  id: number;
  role: string;
  content: string;
  sources: string[];
  rating: number | null;
  eval_faithfulness: number | null;
  eval_relevancy: number | null;
  created_at: string;
};
export type DocItem = { name: string; chunks: number; uploaded_at: string | null };
export type SuggestedQuestionsResponse = { questions: string[] };

export type FieldWorkspace = {
  id: number;
  name: string;
  role: string;
  member_count: number;
};

export type FieldMember = {
  user_id: number;
  email: string;
  role: string;
  base_latitude?: number | null;
  base_longitude?: number | null;
  shift_start?: string;
  shift_end?: string;
};
export type FieldStatus = "open" | "in_progress" | "waiting" | "escalated" | "resolved";
export type FieldStepStatus = "todo" | "done" | "blocked";
export type FieldEvent = EvidenceBundle & {
  id: number;
  actor_id: number;
  event_type: string;
  detail: string;
  created_at: string;
};
export type FieldStep = {
  id: number;
  title: string;
  instruction: string;
  source: string | null;
  status: FieldStepStatus;
  result_note: string | null;
  position: number;
  completed_by: number | null;
  completed_at: string | null;
};
export type FieldCase = {
  id: number;
  workspace_id: number;
  created_by: number;
  assigned_to: number | null;
  title: string;
  customer_label: string | null;
  address: string | null;
  device_model: string | null;
  serial_number: string | null;
  symptom: string;
  status: FieldStatus;
  created_at: string;
  updated_at: string;
  latitude: number | null;
  longitude: number | null;
  priority: FieldPriority;
  window_start: string | null;
  window_end: string | null;
  est_minutes: number;
  scheduled_start: string | null;
  route_order: number | null;
  external_source: string | null;
  external_ref: string | null;
  steps: FieldStep[];
  events: FieldEvent[];
};

export type FieldPriority = "urgent" | "high" | "normal" | "low";

export type FieldPhoto = {
  id: number;
  case_id: number;
  uploaded_by: number;
  caption: string;
  width: number;
  height: number;
  size_bytes: number;
  created_at: string;
  url: string;
  thumb_url: string;
};

export type PlanStop = {
  case_id: number;
  title: string;
  address: string | null;
  latitude: number;
  longitude: number;
  priority: FieldPriority;
  arrive: string;
  start: string;
  end: string;
  travel_minutes: number;
  distance_km: number;
};

export type SchedulePlan = {
  date: string;
  routes: { technician_id: number; technician: string | null; travel_minutes: number; distance_km: number; stops: PlanStop[] }[];
  unassigned: { case_id: number; title: string | null; reason: string }[];
  travel_minutes: number;
  distance_km: number;
  estimate_note: string;
  technicians_without_base: number[];
};

export type FieldIntegration = {
  id: number;
  name: string;
  kind: "crm" | "nms";
  inbound_url: string;
  outbound_url: string | null;
  default_assignee: number | null;
  is_active: boolean;
  deliveries: Record<string, number>;
  last_inbound_at: string | null;
  secret?: string;
};

export type WebhookDelivery = {
  id: number;
  event_type: string;
  status: "pending" | "delivered" | "dead";
  attempts: number;
  next_attempt_at: string;
  last_error: string | null;
  response_code: number | null;
  created_at: string;
  delivered_at: string | null;
};

export type DocLineage = {
  bronze_ingested_at: string | null;
  silver_chunk_count: number;
  validation_passed: boolean | null;
  gold_ready: boolean;
  gold_chunk_count: number | null;
};
export type LineageStatus = { medallion_enabled: boolean; reachable?: boolean };
/** Key "__status__" = trạng thái pipeline chung; các key khác = tên file -> lineage. */
export type LineageMap = Record<string, DocLineage | LineageStatus>;

export async function getDocLineage(): Promise<LineageMap> {
  const res = await api.get<LineageMap>("/documents/lineage");
  return res.data;
}

export async function sendFeedback(convId: number, messageId: number, rating: -1 | 0 | 1) {
  const res = await api.post<Message>(
    `/conversations/${convId}/messages/${messageId}/feedback`,
    { rating }
  );
  return res.data;
}

export async function streamChat(
  convId: number,
  question: string,
  callbacks: {
    onToken: (t: string) => void;
    onDone: (sources: string[], messageId: number | null, evidence?: Evidence[], audit?: CitationAudit | null) => void;
    onError: (e: string) => void;
  },
  source?: string | null
) {
  const token = localStorage.getItem("rag_token");
  const res = await fetch(`${API_URL}/chat/${convId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ question, source: source || null }),
  });

  if (!res.ok || !res.body) {
    if (res.status === 401) expireSession(token);
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
        if (data.done) callbacks.onDone(data.sources ?? [], data.message_id ?? null, data.evidence ?? [], data.citation_audit ?? null);
      } catch {
        /* skip */
      }
    }
  }
}
