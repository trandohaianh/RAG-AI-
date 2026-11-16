import AsyncStorage from "@react-native-async-storage/async-storage";
import { Platform } from "react-native";
import * as SecureStore from "expo-secure-store";
import { rememberOfflineApiOrigin } from "./offlineOrigin";

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
  const parsed = new URL(url.trim());
  if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("Địa chỉ API phải dùng HTTP hoặc HTTPS.");
  const normalized = parsed.toString().replace(/\/$/, "");
  const previous = await getApiUrl();
  if (normalized !== previous) {
    await rememberOfflineApiOrigin(previous);
    await clearAuth();
  }
  await AsyncStorage.setItem(API_URL_KEY, normalized);
  cachedApiUrl = normalized;
}

let authLock: Promise<unknown> = Promise.resolve();
let authRevision = 0;
const authListeners = new Set<(user: User | null) => void>();
function withAuthLock<T>(task: () => Promise<T>): Promise<T> {
  const run = authLock.then(task, task);
  authLock = run.catch(() => undefined);
  return run;
}

export function subscribeAuth(listener: (user: User | null) => void): () => void {
  authListeners.add(listener);
  return () => { authListeners.delete(listener); };
}

function notifyAuth(user: User | null) {
  for (const listener of authListeners) listener(user);
}

async function readToken(): Promise<string | null> {
  if (Platform.OS === "web") return AsyncStorage.getItem(TOKEN_KEY);
  const secure = await SecureStore.getItemAsync(TOKEN_KEY);
  if (secure) return secure;
  const legacy = await AsyncStorage.getItem(TOKEN_KEY);
  if (legacy) {
    // Remove the old plaintext copy only after secure storage succeeds.
    await SecureStore.setItemAsync(TOKEN_KEY, legacy);
    await AsyncStorage.removeItem(TOKEN_KEY);
  }
  return legacy;
}

export function getToken(): Promise<string | null> {
  return withAuthLock(readToken);
}

export type User = {
  id: number;
  email: string;
  role: string;
  subscription_plan?: string;
  created_at: string;
};

export function saveAuth(token: string, user: User): Promise<void> {
  return withAuthLock(async () => {
    authRevision += 1;
    if (Platform.OS === "web") {
      await AsyncStorage.setItem(TOKEN_KEY, token);
    } else {
      await SecureStore.setItemAsync(TOKEN_KEY, token);
      await AsyncStorage.removeItem(TOKEN_KEY);
    }
    await AsyncStorage.setItem(USER_KEY, JSON.stringify(user));
    notifyAuth(user);
  });
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

export function clearAuth(expectedToken?: string | null): Promise<void> {
  return withAuthLock(async () => {
    // A late 401 from an old request must not log out a newer session.
    if (expectedToken !== undefined && await readToken() !== expectedToken) return;
    authRevision += 1;
    try {
      if (Platform.OS !== "web") await SecureStore.deleteItemAsync(TOKEN_KEY);
      await AsyncStorage.multiRemove([TOKEN_KEY, USER_KEY]);
    } finally {
      // Update navigation immediately even if storage is temporarily unavailable.
      notifyAuth(null);
    }
  });
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

export type ApiSession = { apiUrl: string; token: string; userId: number; revision: number };

export function captureSession(userId: number): Promise<ApiSession> {
  return withAuthLock(async () => {
    const token = await readToken();
    const user = await loadUser();
    if (!token || user?.id !== userId) throw new ApiError("Cần đăng nhập lại đúng tài khoản để đồng bộ.", 401);
    return { apiUrl: await getApiUrl(), token, userId, revision: authRevision };
  });
}

export async function isSessionCurrent(session: ApiSession): Promise<boolean> {
  return withAuthLock(async () => session.revision === authRevision &&
    await readToken() === session.token && (await loadUser())?.id === session.userId &&
    await getApiUrl() === session.apiUrl);
}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; auth?: boolean; session?: ApiSession } = {}
): Promise<T> {
  const session = options.session;
  if (session && !(await isSessionCurrent(session))) {
    throw new ApiError("Phiên đăng nhập đã thay đổi; thao tác vẫn được giữ để đồng bộ sau.", 401);
  }
  const base = session?.apiUrl ?? await getApiUrl();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = session?.token ?? (options.auth !== false ? await getToken() : null);
  if (token) headers.Authorization = `Bearer ${token}`;
  if (session && session.revision !== authRevision) {
    throw new ApiError("Phiên đăng nhập đã thay đổi; thao tác vẫn được giữ để đồng bộ sau.", 401);
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
    await clearAuth(token);
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

export type Evidence = { reference_id: string; chunk_id: string | null; source: string; page: number | null;
  document_version: string | null; collection_revision: string | null; content_sha256: string; quote: string };
export type CitationAudit = { status: string; cited_references: string[]; invalid_references: string[]; semantic_support_verified: boolean };
export type EvidenceBundle = { evidence?: Evidence[]; citation_audit?: CitationAudit | null };
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

export async function getMessages(convId: number) {
  return request<Message[]>(`/conversations/${convId}/messages`);
}

export async function sendFeedback(convId: number, messageId: number, rating: -1 | 0 | 1) {
  return request<Message>(`/conversations/${convId}/messages/${messageId}/feedback`, {
    method: "POST",
    body: { rating },
  });
}

// ---- Chat streaming (XHR progressive read on React Native) ----
export function streamChat(
  convId: number,
  question: string,
  callbacks: {
    onToken: (t: string) => void;
    onDone: (sources: string[], messageId: number | null, evidence?: Evidence[], audit?: CitationAudit | null) => void;
    onError: (e: string) => void;
  },
  source?: string | null
): () => void {
  let xhr: XMLHttpRequest | null = null;
  let settled = false;
  const fail = (message: string) => {
    if (settled) return;
    settled = true;
    callbacks.onError(message);
  };
  const cancel = () => {
    fail("Đã dừng câu trả lời.");
    xhr?.abort();
  };

  void (async () => {
    try {
      const base = await getApiUrl();
      const token = await getToken();
      if (settled) return;
      const connection = new XMLHttpRequest();
      xhr = connection;
      let processedLength = 0;
      let buffer = "";

      function consumeLine(line: string) {
        if (settled || !line.startsWith("data:")) return;
        const payload = line.slice(5).trim();
        if (!payload) return;
        let data;
        try {
          data = JSON.parse(payload);
        } catch {
          fail("Máy chủ gửi dữ liệu chat không hợp lệ.");
          return;
        }
        if (data.error) { fail(String(data.error)); return; }
        if (typeof data.token === "string") callbacks.onToken(data.token);
        if (data.done) {
          settled = true;
          callbacks.onDone(data.sources ?? [], data.message_id ?? null, data.evidence ?? [], data.citation_audit ?? null);
        }
      }

      function consume(final = false) {
        if (settled || connection.status < 200 || connection.status >= 300) return;
        buffer += connection.responseText.slice(processedLength);
        processedLength = connection.responseText.length;
        let newline: number;
        while (!settled && (newline = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, newline).replace(/\r$/, "");
          buffer = buffer.slice(newline + 1);
          consumeLine(line);
        }
        if (final && !settled && buffer.trim()) consumeLine(buffer.replace(/\r$/, ""));
      }

      connection.open("POST", `${base}/chat/${convId}`);
      connection.timeout = 180000;
      connection.setRequestHeader("Content-Type", "application/json");
      if (token) connection.setRequestHeader("Authorization", `Bearer ${token}`);
      connection.onprogress = () => consume();
      connection.onerror = () => fail("Lỗi mạng khi nhận câu trả lời. Vui lòng thử lại.");
      connection.ontimeout = () => fail("Máy chủ trả lời quá lâu. Vui lòng thử lại.");
      connection.onabort = () => fail("Đã dừng câu trả lời.");
      connection.onload = () => {
        if (settled) return;
        if (connection.status === 401) {
          void clearAuth(token).catch(() => undefined).finally(() =>
            fail("Phiên đăng nhập hết hạn, vui lòng đăng nhập lại."));
          return;
        }
        if (connection.status < 200 || connection.status >= 300) {
          let message = `Lỗi chat (${connection.status})`;
          try {
            const detail = JSON.parse(connection.responseText).detail;
            if (typeof detail === "string") message = detail;
          } catch { /* Use the HTTP status when the body is not JSON. */ }
          fail(message);
          return;
        }
        consume(true);
        if (!settled) fail("Kết nối kết thúc trước khi nhận đủ câu trả lời. Vui lòng thử lại.");
      };
      connection.send(JSON.stringify({ question, source: source || null }));
    } catch (error) {
      fail(getErrorMessage(error));
    }
  })();
  return cancel;
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
  if (res.status === 401) {
    await clearAuth(token);
    throw new ApiError("Phiên đăng nhập hết hạn, vui lòng đăng nhập lại.", 401);
  }
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

export async function getFieldCase(caseId: number, session?: ApiSession) {
  return request<FieldCase>(`/field/cases/${caseId}`, { session });
}

export async function updateFieldCase(
  caseId: number,
  input: {
    status?: CaseStatus;
    assigned_to?: number | null;
    client_action_id?: string;
  },
  session?: ApiSession
) {
  return request<FieldCase>(`/field/cases/${caseId}`, {
    method: "PATCH",
    body: input,
    session,
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
  },
  session?: ApiSession
) {
  return request<FieldStep>(`/field/cases/${caseId}/steps/${stepId}`, {
    method: "PATCH",
    body: input,
    session,
  });
}

export async function addFieldCaseNote(
  caseId: number,
  detail: string,
  clientActionId?: string,
  session?: ApiSession
) {
  return request<FieldEvent>(`/field/cases/${caseId}/events`, {
    method: "POST",
    body: { detail, client_action_id: clientActionId },
    session,
  });
}

export async function askFieldAssistant(caseId: number, question: string) {
  return request<{ answer: string; sources: string[] } & EvidenceBundle>(
    `/field/cases/${caseId}/assist`,
    { method: "POST", body: { question } }
  );
}

export { ApiError };


export async function getMessageEvidence(convId: number, messageId: number, reference: string) {
  return request<{evidence: Evidence; document_status: string}>(`/conversations/${convId}/messages/${messageId}/evidence/${encodeURIComponent(reference)}`);
}
export async function getFieldEvidence(caseId: number, eventId: number, reference: string) {
  return request<{evidence: Evidence; document_status: string}>(`/field/cases/${caseId}/events/${eventId}/evidence/${encodeURIComponent(reference)}`);
}
