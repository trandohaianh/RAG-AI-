/**
 * Bản lưu offline của danh sách ca (web). Mỗi lần tải thành công thì ghi đè một snapshot theo
 * (API, người dùng, đội); khi mất mạng trang Ca hiện trường hiển thị snapshot này ở chế độ chỉ
 * đọc, còn thay đổi vẫn đi qua hàng đợi offlineQueue như trước. Đăng xuất thì xoá hết.
 */
import type { DocItem, FieldCase, FieldMember, FieldWorkspace } from "@/lib/api";

export type CaseSnapshot = {
  key: string;
  savedAt: string;
  workspaces: FieldWorkspace[];
  workspaceId: number;
  cases: FieldCase[];
  members: FieldMember[];
  docs: DocItem[];
};

const DB = "fieldops-cache";
const STORE = "snapshots";

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "key" });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  try {
    return await new Promise<T>((resolve, reject) => {
      const request = run(db.transaction(STORE, mode).objectStore(STORE));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } finally {
    db.close();
  }
}

export function snapshotKey(apiOrigin: string, userId: number, workspaceId: number | "last") {
  return `${apiOrigin}|${userId}|${workspaceId}`;
}

export async function saveSnapshot(apiOrigin: string, userId: number, data: Omit<CaseSnapshot, "key" | "savedAt">) {
  if (typeof indexedDB === "undefined") return;
  const savedAt = new Date().toISOString();
  try {
    await tx("readwrite", (s) => s.put({ ...data, key: snapshotKey(apiOrigin, userId, data.workspaceId), savedAt }));
    await tx("readwrite", (s) => s.put({ ...data, key: snapshotKey(apiOrigin, userId, "last"), savedAt }));
  } catch {
    /* hết dung lượng / chế độ riêng tư: bỏ qua, không làm hỏng trang */
  }
}

export async function loadSnapshot(
  apiOrigin: string,
  userId: number,
  workspaceId?: number | null,
): Promise<CaseSnapshot | null> {
  if (typeof indexedDB === "undefined") return null;
  try {
    return (await tx("readonly", (s) => s.get(snapshotKey(apiOrigin, userId, workspaceId ?? "last")))) ?? null;
  } catch {
    return null;
  }
}

export async function clearSnapshots() {
  if (typeof indexedDB === "undefined") return;
  try {
    await tx("readwrite", (s) => s.clear());
  } catch {
    /* ignore */
  }
}
