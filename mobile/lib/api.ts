import AsyncStorage from "@react-native-async-storage/async-storage";
import { Platform } from "react-native";

// Địa chỉ Tailscale của laptop — dùng được cả khi ở nhà lẫn ra ngoài (wifi hay 4G),
// vì Tailscale tạo 1 mạng riêng ảo giữa laptop và điện thoại, không phụ thuộc mạng LAN.
// Cổng 8001 (không phải 8000) — cổng 8000 dính lỗi Windows giữ socket "ma" không kill được.
const DEFAULT_API_URL = "http://100.121.99.57:8001";
const API_URL_KEY = "rag_api_url";
const TOKEN_KEY = "rag_token";
const USER_KEY = "rag_user";
const FIELD_WORKSPACE_KEY = "field_workspace_id";

let cachedApiUrl: string | null = null;

export async function getApiUrl(): Promise<string> {
  if (cachedApiUrl) return cachedApiUrl;
  const stored = await AsyncStorage.getItem(API_URL_KEY);
  cachedApiUrl = stored || DEFAULT_API_URL;
  return cachedApiUrl;
}

export async function setApiUrl(url: string): Promise<void> {
  cachedApiUrl = url;
  await AsyncStorage.setItem(API_URL_KEY, url);
}

export async function getToken(): Promise<string | null> {
  return AsyncStorage.getItem(TOKEN_KEY);
}

export type User = {
  id: number;
  email: string;
  role: string;
  subscription_plan?: string;
  created_at: string;
};

export async function saveAuth(token: string, user: User): Promise<void> {
  await AsyncStorage.setItem(TOKEN_KEY, token);
  await AsyncStorage.setItem(USER_KEY, JSON.stringify(user));
}

export async function loadUser(): Promise<User | null> {
  const raw = await AsyncStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

export async function clearAuth(): Promise<void> {
  await AsyncStorage.multiRemove([TOKEN_KEY, USER_KEY]);
}

class ApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.status = status;
  }
}

export function isNetworkError(error: unknown): boolean {
  return error instanceof ApiError && error.status === undefined;
}

export function getApiErrorStatus(error: unknown): number | undefined {
  return error instanceof ApiError ? error.status : undefined;
}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; auth?: boolean } = {}
): Promise<T> {
  const base = await getApiUrl();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (options.auth !== false) {
    const token = await getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      method: options.method || "GET",
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
  } catch {
    throw new ApiError(
      `Không kết nối được API (${base}). Kiểm tra điện thoại và máy tính có chung mạng Wi-Fi không, và backend có đang chạy không.`
    );
  }

  if (res.status === 401 && options.auth !== false) {
    await clearAuth();
    throw new ApiError("Phiên đăng nhập hết hạn, vui lòng đăng nhập lại.", 401);
  }

  if (!res.ok) {
    let detail = `Lỗi ${res.status}`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail)) detail = data.detail.map((d: any) => d.msg ?? d).join(", ");
    } catch {
      /* ignore */
    }
    throw new ApiError(detail, res.status);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

export function getErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return "Lỗi không xác định";
}

// ---- Auth ----
export async function login(email: string, password: string) {
  return request<{ user: User; access_token: string }>("/auth/login", {
    method: "POST",
    body: { email, password },
    auth: false,
  });
}

export async function register(email: string, password: string) {
  return request<{ user: User; access_token: string }>("/auth/register", {
    method: "POST",
    body: { email, password },
    auth: false,
  });
}

// ---- Conversations ----
export type Conversation = { id: number; title: string; created_at: string };

export async function listConversations() {
  return request<Conversation[]>("/conversations");
}

export async function createConversation(title: string) {
  return request<Conversation>("/conversations", { method: "POST", body: { title } });
}

export async function deleteConversation(id: number) {
  return request<void>(`/conversations/${id}`, { method: "DELETE" });
}

export type Message = {
  id: number;
  role: string;
  content: string;
  sources: string[];
  rating: number | null;
  eval_faithfulness: number | null;
  eval_relevancy: number | null;
  created_at: string;
};

export async function getMessages(convId: number) {
  return request<Message[]>(`/conversations/${convId}/messages`);
}

export async function sendFeedback(convId: number, messageId: number, rating: -1 | 0 | 1) {
  return request<Message>(`/conversations/${convId}/messages/${messageId}/feedback`, {
    method: "POST",
    body: { rating },
  });
}

// ---- Chat streaming (XHR progressive read — fetch ReadableStream không ổn định trên RN) ----
export function streamChat(
  convId: number,
  question: string,
  callbacks: {
    onToken: (t: string) => void;
    onDone: (sources: string[], messageId: number | null) => void;
    onError: (e: string) => void;
  },
  source?: string | null
) {
  (async () => {
    const base = await getApiUrl();
    const token = await getToken();
    const xhr = new XMLHttpRequest();
    let processedLength = 0;

    xhr.open("POST", `${base}/chat/${convId}`);
    xhr.setRequestHeader("Content-Type", "application/json");
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    function handleChunk(chunkText: string) {
      const lines = chunkText.split("\n");
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const data = JSON.parse(line.slice(6));
          if (data.error) {
            callbacks.onError(data.error);
            return;
          }
          if (data.token) callbacks.onToken(data.token);
          if (data.done) callbacks.onDone(data.sources ?? [], data.message_id ?? null);
        } catch {
          /* dòng chưa trọn vẹn, bỏ qua */
        }
      }
    }

    xhr.onprogress = () => {
      const newText = xhr.responseText.slice(processedLength);
      processedLength = xhr.responseText.length;
      if (newText) handleChunk(newText);
    };

    xhr.onerror = () => callbacks.onError("Lỗi mạng — kiểm tra backend đang chạy");
    xhr.onload = () => {
      if (xhr.status >= 400) {
        try {
          const err = JSON.parse(xhr.responseText);
          callbacks.onError(err.detail ?? "Lỗi chat");
        } catch {
          callbacks.onError(`Lỗi ${xhr.status}`);
        }
      }
    };

    xhr.send(JSON.stringify({ question, source: source || null }));
  })();
}

// ---- Documents ----
export type DocItem = { name: string; chunks: number; uploaded_at: string | null };

export async function listDocuments(workspaceId?: number) {
  const query = workspaceId ? `?workspace_id=${workspaceId}` : "";
  return request<DocItem[]>(`/documents${query}`);
}

export async function deleteDocument(name: string, workspaceId?: number) {
  const query = workspaceId ? `?workspace_id=${workspaceId}` : "";
  return request<void>(`/documents/${encodeURIComponent(name)}${query}`, { method: "DELETE" });
}

export async function uploadDocument(
  file: { uri: string; name: string; mimeType?: string },
  workspaceId?: number
) {
  const base = await getApiUrl();
  const token = await getToken();
  const form = new FormData();

  if (Platform.OS === "web") {
    // Trên web, FormData cần 1 Blob/File thật — object {uri,name,type} chỉ hoạt động
    // trên React Native native (iOS/Android), không phải trình duyệt.
    const blob = await (await fetch(file.uri)).blob();
    form.append("file", blob, file.name);
  } else {
    form.append("file", {
      uri: file.uri,
      name: file.name,
      type: file.mimeType || "application/octet-stream",
    } as any);
  }

  const query = workspaceId ? `?workspace_id=${workspaceId}` : "";
  const res = await fetch(`${base}/documents/upload${query}`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(data.detail || "Tải lên thất bại", res.status);
  }
  return res.json();
}

// ---- Field operations ----
export type FieldWorkspace = {
  id: number;
  name: string;
  role: string;
  member_count: number;
};

export type FieldMember = { user_id: number; email: string; role: string };
export type CaseStatus = "open" | "in_progress" | "waiting" | "escalated" | "resolved";
export type CaseStepStatus = "todo" | "done" | "blocked";

export type FieldEvent = {
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
  status: CaseStepStatus;
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
  status: CaseStatus;
  created_at: string;
  updated_at: string;
  steps: FieldStep[];
  events: FieldEvent[];
};

export async function listFieldWorkspaces() {
  return request<FieldWorkspace[]>("/field/workspaces");
}

export async function getActiveFieldWorkspace() {
  const workspaces = await listFieldWorkspaces();
  if (!workspaces.length) return null;
  const selectedId = await AsyncStorage.getItem(FIELD_WORKSPACE_KEY);
  const active = workspaces.find((item) => String(item.id) === selectedId) || workspaces[0];
  if (String(active.id) !== selectedId) {
    await AsyncStorage.setItem(FIELD_WORKSPACE_KEY, String(active.id));
  }
  return active;
}

export async function setActiveFieldWorkspace(workspaceId: number) {
  await AsyncStorage.setItem(FIELD_WORKSPACE_KEY, String(workspaceId));
}

export async function createFieldWorkspace(name: string) {
  const workspace = await request<FieldWorkspace>("/field/workspaces", {
    method: "POST",
    body: { name },
  });
  await setActiveFieldWorkspace(workspace.id);
  return workspace;
}

export async function listFieldMembers(workspaceId: number) {
  return request<FieldMember[]>(`/field/workspaces/${workspaceId}/members`);
}

export async function addFieldMember(workspaceId: number, email: string) {
  return request<FieldMember>(`/field/workspaces/${workspaceId}/members`, {
    method: "POST",
    body: { email },
  });
}

export async function listFieldCases(workspaceId: number) {
  return request<FieldCase[]>(`/field/cases?workspace_id=${workspaceId}`);
}

export async function createFieldCase(
  workspaceId: number,
  input: {
    title: string;
    symptom: string;
    customer_label?: string;
    address?: string;
    device_model?: string;
    serial_number?: string;
    assigned_to?: number;
  }
) {
  return request<FieldCase>(`/field/workspaces/${workspaceId}/cases`, {
    method: "POST",
    body: input,
  });
}

export async function getFieldCase(caseId: number) {
  return request<FieldCase>(`/field/cases/${caseId}`);
}

export async function updateFieldCase(
  caseId: number,
  input: {
    status?: CaseStatus;
    assigned_to?: number | null;
    client_action_id?: string;
  }
) {
  return request<FieldCase>(`/field/cases/${caseId}`, {
    method: "PATCH",
    body: input,
  });
}

export async function addFieldCaseStep(
  caseId: number,
  input: {
    title: string;
    instruction?: string;
    source?: string;
    client_action_id?: string;
  }
) {
  return request<FieldStep>(`/field/cases/${caseId}/steps`, {
    method: "POST",
    body: input,
  });
}

export async function updateFieldCaseStep(
  caseId: number,
  stepId: number,
  input: {
    status: CaseStepStatus;
    result_note?: string;
    client_action_id?: string;
  }
) {
  return request<FieldStep>(`/field/cases/${caseId}/steps/${stepId}`, {
    method: "PATCH",
    body: input,
  });
}

export async function addFieldCaseNote(
  caseId: number,
  detail: string,
  clientActionId?: string
) {
  return request<FieldEvent>(`/field/cases/${caseId}/events`, {
    method: "POST",
    body: { detail, client_action_id: clientActionId },
  });
}

export async function askFieldAssistant(caseId: number, question: string) {
  return request<{ answer: string; sources: string[] }>(
    `/field/cases/${caseId}/assist`,
    { method: "POST", body: { question } }
  );
}

export { ApiError };
