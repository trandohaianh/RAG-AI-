import axios from "axios";

function resolveApiUrl(): string {
  const raw = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
  // "/api": API đi qua proxy cùng origin (next.config.js rewrites, Caddy): cookie phiên là
  // first-party nên không bị Safari/Firefox chặn như khi web và API ở hai site khác nhau.
  if (raw.startsWith("/")) return raw;
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  return `https://${raw}`;
}

// Dùng chung cho mọi lời gọi không qua axios (XHR upload, fetch SSE): Render truyền host không
// có scheme, nên ghép thẳng process.env tạo URL tương đối hỏng.
export const API_URL = resolveApiUrl();

/**
 * Phiên web nằm trong cookie httpOnly do API đặt (JS không đọc được token → XSS không lấy
 * được phiên). Mọi request gửi kèm cookie, cùng hai header:
 * - X-Auth-Mode: cookie   → khi đăng nhập, server đặt cookie thay vì trả token trong body;
 * - X-Requested-With      → bắt buộc với phương thức ghi khi xác thực bằng cookie (chống CSRF).
 */
export const SESSION_HEADERS = { "X-Requested-With": "fieldops", "X-Auth-Mode": "cookie" } as const;
/** Mã phiên ngẫu nhiên đặt lúc đăng nhập: không phải bí mật, chỉ để biết một phản hồi 401
 * thuộc phiên nào (phiên mới hơn không bị phản hồi trễ của phiên cũ đăng xuất nhầm). */
export const SESSION_KEY = "rag_session";
export const USER_KEY = "rag_user";
const LEGACY_TOKEN_KEY = "rag_token";

const api = axios.create({
  baseURL: API_URL,
  headers: { "Content-Type": "application/json", ...SESSION_HEADERS },
  timeout: 15000,
  withCredentials: true,
});

export function currentSession(): string | null {
  return typeof window === "undefined" ? null : localStorage.getItem(SESSION_KEY);
}

api.interceptors.request.use((config) => {
  (config as typeof config & { sessionEpoch?: string | null }).sessionEpoch = currentSession();
  return config;
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

/** Token cũ từng nằm trong localStorage (trước khi chuyển sang cookie). Trả về rồi xoá. */
export function takeLegacyToken(): string | null {
  const token = peekLegacyToken();
  if (token) dropLegacyToken();
  return token;
}

export function peekLegacyToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(LEGACY_TOKEN_KEY);
}

export function dropLegacyToken(): void {
  if (typeof window !== "undefined") localStorage.removeItem(LEGACY_TOKEN_KEY);
}

function expireSession(epoch: string | null | undefined) {
  if (typeof window === "undefined" || !epoch || currentSession() !== epoch) return;
  localStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(USER_KEY);
  // Cookie không còn hợp lệ: xoá nó luôn (không cần chờ kết quả).
  void fetch(`${API_URL}/auth/logout`, { method: "POST", credentials: "include", headers: SESSION_HEADERS }).catch(
    () => undefined,
  );
  // Tải lại hẳn trang (không dùng router): xoá mọi state trong bộ nhớ của phiên vừa hết hạn.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  if (!window.location.pathname.startsWith("/login")) window.location.href = "/login";
}

// Phiên hết hạn/không hợp lệ (401) → tự đăng xuất thay vì báo lỗi mãi trong khi UI vẫn tưởng
// đang đăng nhập. Chỉ đăng xuất phiên đã gửi request đó, không phải phiên mới hơn.
api.interceptors.response.use(
  (res) => res,
  (err) => {
    const status = err.response?.status;
    const url: string = err.config?.url ?? "";
    const isAuthEndpoint = url.includes("/auth/login") || url.includes("/auth/register");
    if (status === 401 && !isAuthEndpoint) expireSession(err.config?.sessionEpoch);
    return Promise.reject(err);
  },
);

export default api;

export type Evidence = {
  reference_id: string;
  chunk_id: string | null;
  source: string;
  page: number | null;
  document_version: string | null;
  collection_revision: string | null;
  content_sha256: string;
  quote: string;
};
export type CitationAudit = {
  status: string;
  cited_references: string[];
  invalid_references: string[];
  semantic_support_verified: boolean;
};
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
  /** Dấu phiên bản thông tin ca — gửi lại khi sửa (M14) để phát hiện người khác vừa sửa. */
  details_version: string;
  /** M12 — biên bản đóng ca hiệu lực (chỉ khi ca đã hoàn tất). */
  closure: FieldClosure | null;
  steps: FieldStep[];
  events: FieldEvent[];
};

export type FieldPriority = "urgent" | "high" | "normal" | "low";

export type FieldClosure = {
  id: number;
  outcome_code: string;
  outcome_label: string;
  note: string;
  customer_name: string | null;
  signature_photo_id: number | null;
  signed: boolean;
  signature_url: string | null;
  closed_by: number;
  closed_at: string;
};

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
  routes: {
    technician_id: number;
    technician: string | null;
    travel_minutes: number;
    distance_km: number;
    stops: PlanStop[];
  }[];
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
  const res = await api.post<Message>(`/conversations/${convId}/messages/${messageId}/feedback`, { rating });
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
  source?: string | null,
) {
  const epoch = currentSession();
  const res = await fetch(`${API_URL}/chat/${convId}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...SESSION_HEADERS },
    body: JSON.stringify({ question, source: source || null }),
  });

  if (!res.ok || !res.body) {
    if (res.status === 401) expireSession(epoch);
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
        if (data.done)
          callbacks.onDone(
            data.sources ?? [],
            data.message_id ?? null,
            data.evidence ?? [],
            data.citation_audit ?? null,
          );
      } catch {
        /* skip */
      }
    }
  }
}
