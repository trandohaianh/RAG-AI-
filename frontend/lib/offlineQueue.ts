/**
 * Durable outbox for field actions. Records survive logout and are only replayed
 * for their original API, account and workspace. Legacy records without an owner
 * are retained for recovery, never automatically attributed to the current user.
 */
export type QueueScope = { apiOrigin: string; userId: number; workspaceId: number };
export type QueuedAction = {
  id: string;
  scope: QueueScope;
  method: "post" | "patch";
  url: string;
  body: Record<string, unknown>;
  label: string;
  createdAt: number;
  state?: "pending" | "rejected";
  error?: string;
  httpStatus?: number;
  /** Số lần liên tiếp máy chủ trả 5xx cho chính thao tác này. */
  serverFailures?: number;
};

/** Quá số lần 5xx này, thao tác được chuyển sang "bị từ chối" để người dùng xem lại: một
 * payload mà máy chủ luôn trả 500 không được chặn mãi mọi thao tác xếp sau nó. Lỗi mạng
 * (đang offline) không bị đếm. */
export const MAX_SERVER_FAILURES = 8;

const DB_NAME = "fieldops";
const STORE = "pending";
// Keep the existing store: old records must not disappear during an upgrade.
const VERSION = 1;
type Sender = (method: "post" | "patch", url: string, body: Record<string, unknown>) => Promise<unknown>;

export function createQueueScope(apiUrl: string, userId: number, workspaceId: number): QueueScope {
  // API cùng origin ("/api"): URL tương đối, lấy origin của trang làm gốc.
  const base = typeof window !== "undefined" && window.location?.origin ? window.location.origin : "http://localhost";
  return { apiOrigin: new URL(apiUrl, base).origin, userId, workspaceId };
}

function scopeKey(scope: QueueScope): string {
  return JSON.stringify([scope.apiOrigin, scope.userId, scope.workspaceId]);
}

function owns(action: QueuedAction, scope: QueueScope): boolean {
  return !!action.scope && scopeKey(action.scope) === scopeKey(scope);
}

function open(): Promise<IDBDatabase> {
  if (typeof window === "undefined" || !("indexedDB" in window)) {
    return Promise.reject(new Error("Trình duyệt không hỗ trợ lưu thao tác offline. Nội dung chưa được lưu."));
  }
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, VERSION);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE, { keyPath: "id" }).createIndex("seq", "createdAt");
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("Không mở được bộ nhớ offline."));
    req.onblocked = () => reject(new Error("Bộ nhớ offline đang bị khóa bởi tab khác."));
  });
}

async function tx<T>(mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  return new Promise<T>((resolve, reject) => {
    let result: T;
    let requestError: DOMException | null = null;
    const t = db.transaction(STORE, mode);
    t.oncomplete = () => {
      db.close();
      resolve(result);
    };
    t.onabort = () => {
      db.close();
      reject(requestError ?? t.error ?? new Error("Không lưu được thao tác offline; giao dịch đã hủy."));
    };
    t.onerror = () => {
      requestError = t.error;
    };
    try {
      const req = run(t.objectStore(STORE));
      req.onsuccess = () => {
        result = req.result;
      };
      req.onerror = () => {
        requestError = req.error;
      };
    } catch (error) {
      t.abort();
      reject(error);
    }
  });
}

export function newActionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `a-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

async function all(): Promise<QueuedAction[]> {
  return tx("readonly", (s) => s.getAll() as IDBRequest<QueuedAction[]>);
}

export async function pending(scope: QueueScope): Promise<QueuedAction[]> {
  return (await all())
    .filter((a) => owns(a, scope) && a.state !== "rejected")
    .sort((a, b) => a.createdAt - b.createdAt);
}

export async function rejected(scope: QueueScope): Promise<QueuedAction[]> {
  return (await all())
    .filter((a) => owns(a, scope) && a.state === "rejected")
    .sort((a, b) => a.createdAt - b.createdAt);
}

export async function legacyPendingCount(): Promise<number> {
  return (await all()).filter((a) => !a.scope).length;
}

async function put(action: QueuedAction): Promise<void> {
  await tx("readwrite", (s) => s.put(action));
}

async function drop(id: string): Promise<void> {
  await tx("readwrite", (s) => s.delete(id));
}

function retryable(err: unknown): boolean {
  const status = (err as { response?: { status?: number } })?.response?.status;
  return status === undefined || status === 401 || status === 408 || status === 429 || status >= 500;
}

// Serialise send/retry/flush in this tab; Web Locks also serialises other tabs.
// Backend idempotency remains the final protection on browsers without Web Locks.
const locks = new Map<string, Promise<unknown>>();
function withScopeLock<T>(scope: QueueScope, task: () => Promise<T>): Promise<T> {
  const key = scopeKey(scope);
  const previous = locks.get(key) ?? Promise.resolve();
  const run: Promise<T> = previous
    .catch(() => undefined)
    .then(async () => {
      if (typeof navigator !== "undefined" && navigator.locks) {
        return await navigator.locks.request(`fieldops:${key}`, task);
      }
      return await task();
    });
  locks.set(key, run);
  void run
    .finally(() => {
      if (locks.get(key) === run) locks.delete(key);
    })
    .catch(() => undefined);
  return run;
}

async function runFlush(scope: QueueScope, sender: Sender) {
  let sent = 0;
  for (const action of await pending(scope)) {
    if (typeof navigator !== "undefined" && navigator.onLine === false) break;
    try {
      await sender(action.method, action.url, action.body);
    } catch (err) {
      const response = (err as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
      const status = response?.status;
      if (retryable(err)) {
        if (status === undefined || status < 500) break;
        const failures = (action.serverFailures ?? 0) + 1;
        if (failures < MAX_SERVER_FAILURES) {
          await put({ ...action, serverFailures: failures });
          break;
        }
        await put({
          ...action,
          serverFailures: failures,
          state: "rejected",
          httpStatus: status,
          error: `Máy chủ lỗi ${status} ${failures} lần liên tiếp với thao tác này. Hãy thử lại sau hoặc huỷ.`,
        });
        continue;
      }
      const detail = response?.data?.detail;
      await put({
        ...action,
        state: "rejected",
        error: typeof detail === "string" ? detail : err instanceof Error ? err.message : "Máy chủ từ chối thao tác.",
        httpStatus: response?.status,
      });
      continue;
    }
    // A delete failure leaves the original ID queued: replay is still idempotent.
    await drop(action.id);
    sent += 1;
  }
  return { sent, left: (await pending(scope)).length, rejected: (await rejected(scope)).length };
}

export function flush(scope: QueueScope, sender: Sender) {
  return withScopeLock(scope, () => runFlush(scope, sender));
}

export function send(
  scope: QueueScope,
  sender: Sender,
  method: "post" | "patch",
  url: string,
  body: Record<string, unknown>,
  label: string,
): Promise<{ queued: boolean }> {
  return withScopeLock(scope, async () => {
    const id = newActionId();
    const action: QueuedAction = {
      id,
      scope,
      method,
      url,
      label,
      createdAt: Date.now(),
      state: "pending",
      body: { ...body, client_action_id: body.client_action_id ?? id },
    };
    // Persist BEFORE the request so a tab closing mid-request cannot lose an edit.
    await put(action);
    await runFlush(scope, sender);
    const remaining = (await all()).find((entry) => entry.id === id);
    if (remaining?.state === "rejected") throw new Error(remaining.error);
    return { queued: !!remaining };
  });
}

export function retryRejected(scope: QueueScope, id: string): Promise<void> {
  return withScopeLock(scope, async () => {
    const action = (await rejected(scope)).find((entry) => entry.id === id);
    if (action) await put({ ...action, state: "pending", error: undefined, httpStatus: undefined, serverFailures: 0 });
  });
}

export function discardRejected(scope: QueueScope, id: string): Promise<void> {
  return withScopeLock(scope, async () => {
    if ((await rejected(scope)).some((entry) => entry.id === id)) await drop(id);
  });
}
