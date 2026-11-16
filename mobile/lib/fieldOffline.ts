import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";
import * as api from "./api";
import { assertOfflineApiOrigin, bindOfflineApiOriginForWrite } from "./offlineOrigin";

const STORAGE_PREFIX = "fieldops.offline.v1";
const CHUNK_BYTE_LIMIT = 1200;
const MAX_CACHED_CASES = 80;

/** Quá số lần máy chủ trả 5xx liên tiếp cho một thao tác thì chuyển nó sang "bị từ chối":
 * một payload mà máy chủ luôn lỗi không được chặn mãi các thao tác xếp sau trong cùng ca.
 * Lỗi mạng (đang offline) không bị đếm. */
export const MAX_SERVER_FAILURES = 8;

export type PendingFieldAction = { server_failures?: number } & (
  | {
      id: string;
      case_id: number;
      created_at: string;
      kind: "status";
      status: api.CaseStatus;
    }
  | {
      id: string;
      case_id: number;
      created_at: string;
      kind: "step_update";
      step_id: number;
      status: api.CaseStepStatus;
      result_note?: string;
    }
  | {
      id: string;
      case_id: number;
      created_at: string;
      kind: "note";
      detail: string;
    });

type NewPendingFieldAction = PendingFieldAction extends infer Action
    ? Action extends PendingFieldAction
      ? Omit<Action, "id" | "created_at">
      : never
    : never;

export type CachedFieldOverview = {
  workspace: api.FieldWorkspace;
  cases: api.FieldCase[];
  cached_at: string;
};

type RecordManifest = { generation: string; chunks: number };

// SecureStore has no transactions, so every read-modify-write of offline
// records goes through this queue. Never hold it across a network call.
let storageLock: Promise<unknown> = Promise.resolve();

function withStorageLock<T>(task: () => Promise<T>): Promise<T> {
  const run = storageLock.then(task, task);
  storageLock = run.catch(() => undefined);
  return run;
}

function recordKey(...parts: Array<string | number>): string {
  return `${STORAGE_PREFIX}.${parts.join(".")}`;
}

function utf8Size(character: string): number {
  const codePoint = character.codePointAt(0) ?? 0;
  if (codePoint <= 0x7f) return 1;
  if (codePoint <= 0x7ff) return 2;
  if (codePoint <= 0xffff) return 3;
  return 4;
}

function splitIntoSecureChunks(value: string): string[] {
  const chunks: string[] = [];
  let chunk = "";
  let bytes = 0;
  for (const character of value) {
    const size = utf8Size(character);
    if (bytes + size > CHUNK_BYTE_LIMIT && chunk) {
      chunks.push(chunk);
      chunk = "";
      bytes = 0;
    }
    chunk += character;
    bytes += size;
  }
  if (chunk || !chunks.length) chunks.push(chunk);
  return chunks;
}

async function ensureSecureStorage(mode: "read" | "write" = "read"): Promise<void> {
  if (Platform.OS === "web" || !(await SecureStore.isAvailableAsync())) {
    throw new Error("Bộ nhớ offline an toàn chỉ khả dụng trên thiết bị iOS/Android.");
  }
  const apiUrl = await api.getApiUrl();
  // Chỉ ghi mới gắn vùng offline với máy chủ; đọc chỉ kiểm tra.
  if (mode === "write") await bindOfflineApiOriginForWrite(apiUrl);
  else await assertOfflineApiOrigin(apiUrl);
}

async function removeGeneration(key: string, generation: string, count: number) {
  await Promise.all(
    Array.from({ length: count }, (_, index) =>
      SecureStore.deleteItemAsync(`${key}.${generation}.${index}`)
    )
  );
}

async function writeRecord<T>(key: string, value: T): Promise<void> {
  await ensureSecureStorage("write");
  const manifestKey = `${key}.manifest`;
  const oldRaw = await SecureStore.getItemAsync(manifestKey);
  let oldManifest: RecordManifest | null = null;
  if (oldRaw) {
    try {
      oldManifest = JSON.parse(oldRaw) as RecordManifest;
    } catch {
      oldManifest = null;
    }
  }

  const generation = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  const chunks = splitIntoSecureChunks(JSON.stringify(value));
  try {
    for (let index = 0; index < chunks.length; index += 1) {
      await SecureStore.setItemAsync(`${key}.${generation}.${index}`, chunks[index]);
    }
    await SecureStore.setItemAsync(
      manifestKey,
      JSON.stringify({ generation, chunks: chunks.length } satisfies RecordManifest)
    );
  } catch (error) {
    await removeGeneration(key, generation, chunks.length);
    throw error;
  }

  if (oldManifest) {
    await removeGeneration(key, oldManifest.generation, oldManifest.chunks);
  }
}

async function readRecord<T>(key: string): Promise<T | null> {
  await ensureSecureStorage();
  const raw = await SecureStore.getItemAsync(`${key}.manifest`);
  if (!raw) return null;

  let manifest: RecordManifest;
  try {
    manifest = JSON.parse(raw) as RecordManifest;
  } catch {
    throw new Error("Dữ liệu offline bị lỗi; cần kết nối mạng để tải lại.");
  }
  if (
    typeof manifest.generation !== "string" ||
    !Number.isInteger(manifest.chunks) ||
    manifest.chunks < 1 ||
    manifest.chunks > 10000
  ) {
    throw new Error("Dữ liệu offline không hợp lệ; cần kết nối mạng để tải lại.");
  }

  const chunks: string[] = [];
  for (let index = 0; index < manifest.chunks; index += 1) {
    const chunk = await SecureStore.getItemAsync(
      `${key}.${manifest.generation}.${index}`
    );
    if (chunk === null) {
      throw new Error("Bản lưu offline chưa đầy đủ; cần kết nối mạng để tải lại.");
    }
    chunks.push(chunk);
  }
  try {
    return JSON.parse(chunks.join("")) as T;
  } catch {
    throw new Error("Không đọc được dữ liệu offline; cần kết nối mạng để tải lại.");
  }
}

async function removeRecord(key: string): Promise<void> {
  await ensureSecureStorage();
  const manifestKey = `${key}.manifest`;
  const raw = await SecureStore.getItemAsync(manifestKey);
  if (raw) {
    try {
      const manifest = JSON.parse(raw) as RecordManifest;
      await removeGeneration(key, manifest.generation, manifest.chunks);
    } catch (error) {
      throw new Error(`Không thể xóa bản lưu offline: ${api.getErrorMessage(error)}`);
    }
  }
  await SecureStore.deleteItemAsync(manifestKey);
}

export async function isSecureOfflineStorageAvailable(): Promise<boolean> {
  if (Platform.OS === "web" || !(await SecureStore.isAvailableAsync())) return false;
  try {
    await assertOfflineApiOrigin(await api.getApiUrl());
    return true;
  } catch {
    // Keep the original server's data private and intact on another backend.
    return false;
  }
}

export async function cacheFieldOverview(
  userId: number,
  workspace: api.FieldWorkspace,
  cases: api.FieldCase[]
): Promise<void> {
  const overview: CachedFieldOverview = {
    workspace,
    cases: cases.slice(0, 50).map((fieldCase) => ({
      ...fieldCase,
      events: fieldCase.events.slice(-8),
    })),
    cached_at: new Date().toISOString(),
  };
  await withStorageLock(async () => {
    await writeRecord(recordKey(userId, workspace.id, "overview"), overview);
    await writeRecord(recordKey(userId, "active_workspace"), workspace.id);
  });
}

// Đọc cũng đi qua khoá: writeRecord xoá generation cũ SAU khi đổi manifest, nên một lượt đọc
// chen giữa có thể gặp chunk đã bị xoá ("Bản lưu offline chưa đầy đủ") dù dữ liệu vẫn nguyên.
export function getCachedActiveFieldOverview(
  userId: number
): Promise<CachedFieldOverview | null> {
  return withStorageLock(async () => {
    const workspaceId = await readRecord<number>(
      recordKey(userId, "active_workspace")
    );
    if (!workspaceId) return null;
    return readRecord<CachedFieldOverview>(recordKey(userId, workspaceId, "overview"));
  });
}

export function cacheFieldCase(
  userId: number,
  workspaceId: number,
  fieldCase: api.FieldCase
): Promise<void> {
  return withStorageLock(() => cacheFieldCaseUnlocked(userId, workspaceId, fieldCase));
}

async function cacheFieldCaseUnlocked(
  userId: number,
  workspaceId: number,
  fieldCase: api.FieldCase
): Promise<void> {
  await writeRecord(
    recordKey(userId, "case", fieldCase.id),
    fieldCase
  );
  const indexKey = recordKey(userId, "case_index");
  const cachedRefs =
    (await readRecord<Array<{ id: number; workspace_id: number }>>(indexKey)) ?? [];
  const nextRefs = [
    ...cachedRefs.filter((entry) => entry.id !== fieldCase.id),
    { id: fieldCase.id, workspace_id: workspaceId },
  ];
  const evicted = nextRefs.splice(0, Math.max(0, nextRefs.length - MAX_CACHED_CASES));
  await writeRecord(indexKey, nextRefs);
  for (const entry of evicted) {
    await removeRecord(recordKey(userId, "case", entry.id));
  }
  const overview = await readRecord<CachedFieldOverview>(
    recordKey(userId, workspaceId, "overview")
  );
  if (overview) {
    overview.cases = overview.cases.map((cached) =>
      cached.id === fieldCase.id
        ? { ...fieldCase, events: fieldCase.events.slice(-8) }
        : cached
    );
    overview.cached_at = new Date().toISOString();
    await writeRecord(recordKey(userId, workspaceId, "overview"), overview);
  }
}

export function getCachedFieldCase(
  userId: number,
  caseId: number
): Promise<api.FieldCase | null> {
  return withStorageLock(() => readRecord<api.FieldCase>(recordKey(userId, "case", caseId)));
}

export function removeCachedFieldCase(
  userId: number,
  caseId: number
): Promise<void> {
  return withStorageLock(() => removeCachedFieldCaseUnlocked(userId, caseId));
}

async function removeCachedFieldCaseUnlocked(
  userId: number,
  caseId: number
): Promise<void> {
  const indexKey = recordKey(userId, "case_index");
  const cachedRefs =
    (await readRecord<Array<{ id: number; workspace_id: number }>>(indexKey)) ?? [];
  const entry = cachedRefs.find((cached) => cached.id === caseId);
  await removeRecord(recordKey(userId, "case", caseId));
  await writeRecord(
    indexKey,
    cachedRefs.filter((cached) => cached.id !== caseId)
  );
  // A case can exist only in the overview before its detail is ever opened.
  const activeWorkspace = await readRecord<number>(recordKey(userId, "active_workspace"));
  const workspaceIds = new Set(cachedRefs.map((cached) => cached.workspace_id));
  if (entry) workspaceIds.add(entry.workspace_id);
  if (activeWorkspace) workspaceIds.add(activeWorkspace);
  for (const workspaceId of workspaceIds) {
    const overviewKey = recordKey(userId, workspaceId, "overview");
    const overview = await readRecord<CachedFieldOverview>(overviewKey);
    if (overview?.cases.some((fieldCase) => fieldCase.id === caseId)) {
      overview.cases = overview.cases.filter((fieldCase) => fieldCase.id !== caseId);
      await writeRecord(overviewKey, overview);
    }
  }
}

function makeActionId(): string {
  return `field-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export function prepareFieldAction<Action extends NewPendingFieldAction>(
  action: Action
): Action & Pick<PendingFieldAction, "id" | "created_at"> {
  return {
    ...action,
    id: makeActionId(),
    created_at: new Date().toISOString(),
  };
}

export function enqueueFieldAction(
  userId: number,
  action: PendingFieldAction
): Promise<PendingFieldAction> {
  return withStorageLock(() => enqueueFieldActionUnlocked(userId, action));
}

async function enqueueFieldActionUnlocked(
  userId: number,
  action: PendingFieldAction
): Promise<PendingFieldAction> {
  const key = recordKey(userId, "actions");
  const queue = (await readRecord<PendingFieldAction[]>(key)) ?? [];
  if (queue.some((entry) => entry.id === action.id)) return action;
  await writeRecord(key, [...queue, action]);
  return action;
}

export function getPendingFieldActionCount(userId: number): Promise<number> {
  return withStorageLock(async () =>
    (await readRecord<PendingFieldAction[]>(recordKey(userId, "actions")))?.length ?? 0);
}

export type RejectedFieldAction = PendingFieldAction & {
  rejected_at: string;
  error: string;
  http_status?: number;
};

// 4xx means the server will never accept this exact request, except for
// timeouts, rate limits and expired sessions, which are worth retrying.
function isPermanentRejection(error: unknown): boolean {
  const status = api.getApiErrorStatus(error);
  return (
    status !== undefined &&
    status >= 400 &&
    status < 500 &&
    status !== 401 &&
    status !== 408 &&
    status !== 429
  );
}

async function sendFieldAction(action: PendingFieldAction, session: api.ApiSession): Promise<void> {
  if (action.kind === "status") {
    await api.updateFieldCase(action.case_id, {
      status: action.status,
      client_action_id: action.id,
    }, session);
  } else if (action.kind === "step_update") {
    await api.updateFieldCaseStep(action.case_id, action.step_id, {
      status: action.status,
      result_note: action.result_note,
      client_action_id: action.id,
    }, session);
  } else {
    await api.addFieldCaseNote(action.case_id, action.detail, action.id, session);
  }
}

export async function getRejectedFieldActions(
  userId: number,
  caseId?: number
): Promise<RejectedFieldAction[]> {
  const rejected =
    (await withStorageLock(() =>
      readRecord<RejectedFieldAction[]>(recordKey(userId, "rejected_actions")))) ?? [];
  return caseId === undefined
    ? rejected
    : rejected.filter((action) => action.case_id === caseId);
}

export function discardRejectedFieldAction(
  userId: number,
  actionId: string
): Promise<void> {
  return withStorageLock(async () => {
    const key = recordKey(userId, "rejected_actions");
    const rejected = (await readRecord<RejectedFieldAction[]>(key)) ?? [];
    await writeRecord(key, rejected.filter((action) => action.id !== actionId));
  });
}

export function retryRejectedFieldAction(
  userId: number,
  actionId: string
): Promise<void> {
  return withStorageLock(async () => {
    const key = recordKey(userId, "rejected_actions");
    const rejected = (await readRecord<RejectedFieldAction[]>(key)) ?? [];
    const entry = rejected.find((action) => action.id === actionId);
    if (!entry) return;
    const { rejected_at: _at, error: _error, http_status: _status, server_failures: _failures, ...action } = entry;
    await enqueueFieldActionUnlocked(userId, action as PendingFieldAction);
    await writeRecord(key, rejected.filter((item) => item.id !== actionId));
  });
}

type SyncResult = { pending: number; rejected: number; offline: boolean; errors: string[] };

const syncInFlight = new Map<string, Promise<SyncResult>>();

// Share one run only for the same server and owner.
export async function syncPendingFieldActions(userId: number): Promise<SyncResult> {
  const key = `${await api.getApiUrl()}:${userId}`;
  const running = syncInFlight.get(key);
  if (running) return running;
  const run = runFieldSync(userId).finally(() => syncInFlight.delete(key));
  syncInFlight.set(key, run);
  return run;
}

async function runFieldSync(userId: number): Promise<SyncResult> {
  const session = await api.captureSession(userId);
  const key = recordKey(userId, "actions");
  const rejectedKey = recordKey(userId, "rejected_actions");
  // Snapshot only; actions queued meanwhile are picked up by the next sync.
  const snapshot = (await withStorageLock(() => readRecord<PendingFieldAction[]>(key))) ?? [];
  const errors: string[] = [];
  const caseIds = Array.from(new Set(snapshot.map((action) => action.case_id)));
  let offline = false;

  const settle = (action: PendingFieldAction, rejection?: RejectedFieldAction) =>
    withStorageLock(async () => {
      if (rejection) {
        const rejected = (await readRecord<RejectedFieldAction[]>(rejectedKey)) ?? [];
        await writeRecord(rejectedKey, [...rejected, rejection]);
      }
      const queue = (await readRecord<PendingFieldAction[]>(key)) ?? [];
      await writeRecord(key, queue.filter((queued) => queued.id !== action.id));
    });

  cases: for (const caseId of caseIds) {
    const group = snapshot.filter((action) => action.case_id === caseId);
    let delivered = false;
    for (const action of group) {
      if (!(await api.isSessionCurrent(session))) {
        errors.push("Phiên đăng nhập đã thay đổi; thao tác vẫn được giữ trên thiết bị.");
        break cases;
      }
      try {
        await sendFieldAction(action, session);
      } catch (error) {
        if (api.isNetworkError(error)) {
          offline = true;
          break;
        }
        errors.push(api.getErrorMessage(error));
        if (!isPermanentRejection(error)) {
          const status = api.getApiErrorStatus(error);
          if (status !== undefined && status >= 500) {
            const failures = (action.server_failures ?? 0) + 1;
            if (failures >= MAX_SERVER_FAILURES) {
              await settle(action, {
                ...action,
                server_failures: failures,
                rejected_at: new Date().toISOString(),
                error: `Máy chủ lỗi ${status} ${failures} lần liên tiếp: ${api.getErrorMessage(error)}`,
                http_status: status,
              });
              continue;
            }
            await withStorageLock(async () => {
              const queue = (await readRecord<PendingFieldAction[]>(key)) ?? [];
              await writeRecord(key, queue.map((queued) =>
                queued.id === action.id ? { ...queued, server_failures: failures } : queued));
            });
          }
          // Keep this and later actions of the case queued, in order.
          break;
        }
        await settle(action, {
          ...action,
          rejected_at: new Date().toISOString(),
          error: api.getErrorMessage(error),
          http_status: api.getApiErrorStatus(error),
        });
        continue;
      }
      delivered = true;
      await settle(action);
    }
    if (offline) break;
    if (!delivered) continue;

    let refreshed: api.FieldCase;
    try {
      refreshed = await api.getFieldCase(caseId, session);
    } catch (error) {
      if (api.isNetworkError(error)) {
        offline = true;
        break;
      }
      continue;
    }
    await withStorageLock(async () => {
      // Re-apply anything still queued for this case so the cached copy keeps
      // edits the technician made while this sync was running.
      const queue = (await readRecord<PendingFieldAction[]>(key)) ?? [];
      const merged = queue
        .filter((action) => action.case_id === caseId)
        .reduce(
          (fieldCase, action) => applyPendingFieldAction(fieldCase, action, userId),
          refreshed
        );
      await cacheFieldCaseUnlocked(userId, merged.workspace_id, merged);
    });
  }

  const [queue, rejected] = await withStorageLock(() =>
    Promise.all([
      readRecord<PendingFieldAction[]>(key),
      readRecord<RejectedFieldAction[]>(rejectedKey),
    ])
  );
  return {
    pending: queue?.length ?? 0,
    rejected: rejected?.length ?? 0,
    offline,
    errors,
  };
}

export function applyPendingFieldAction(
  fieldCase: api.FieldCase,
  action: PendingFieldAction,
  actorId: number
): api.FieldCase {
  const updated: api.FieldCase = {
    ...fieldCase,
    steps: fieldCase.steps.map((step) => ({ ...step })),
    events: [...fieldCase.events],
  };
  const createdAt = action.created_at;
  const eventId = -(updated.events.filter((event) => event.id < 0).length + 1);
  if (action.kind === "status") {
    updated.status = action.status;
    updated.events.push({
      id: eventId,
      actor_id: actorId,
      event_type: "status",
      detail: `Đang chờ đồng bộ: chuyển trạng thái ${action.status}`,
      created_at: createdAt,
    });
  } else if (action.kind === "step_update") {
    updated.steps = updated.steps.map((step) =>
      step.id === action.step_id
        ? {
            ...step,
            status: action.status,
            result_note: action.result_note ?? null,
            completed_by: action.status === "done" ? actorId : null,
            completed_at: action.status === "done" ? createdAt : null,
          }
        : step
    );
    updated.events.push({
      id: eventId,
      actor_id: actorId,
      event_type: "step_updated",
      detail: `Đang chờ đồng bộ: cập nhật checklist #${action.step_id}`,
      created_at: createdAt,
    });
  } else {
    updated.events.push({
      id: eventId,
      actor_id: actorId,
      event_type: "field_note",
      detail: `Đang chờ đồng bộ: ${action.detail}`,
      created_at: createdAt,
    });
  }
  return updated;
}
