/**
 * Hàng chờ ảnh hiện trường khi mất sóng.
 *
 * Ảnh từ camera nằm trong thư mục cache (hệ điều hành có thể dọn), nên trước khi xếp hàng ta
 * chép sang thư mục tài liệu của app. Danh sách chờ (không chứa ảnh, chỉ đường dẫn + chú thích)
 * lưu trong SecureStore theo từng người dùng. Gửi lại dùng cùng client_action_id nên máy chủ
 * không tạo ảnh trùng dù app gửi 2 lần.
 */
import * as SecureStore from "expo-secure-store";
import * as FileSystem from "expo-file-system/legacy";
import { Platform } from "react-native";
import * as api from "./api";

export type QueuedPhoto = { id: string; caseId: number; path: string; caption: string; createdAt: string };

const key = (userId: number) => `fieldops.photos.v1.${userId}`;
const DIR = `${FileSystem.documentDirectory ?? ""}field-photo-outbox/`;

async function read(userId: number): Promise<QueuedPhoto[]> {
  try {
    return JSON.parse((await SecureStore.getItemAsync(key(userId))) || "[]");
  } catch {
    return [];
  }
}

async function write(userId: number, items: QueuedPhoto[]) {
  await SecureStore.setItemAsync(key(userId), JSON.stringify(items));
}

export function newPhotoActionId(): string {
  return `photo-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export async function queuePhoto(userId: number, caseId: number, uri: string, caption: string, id: string) {
  if (Platform.OS === "web" || !FileSystem.documentDirectory) {
    throw new Error("Thiết bị này không lưu được ảnh để gửi sau");
  }
  await FileSystem.makeDirectoryAsync(DIR, { intermediates: true }).catch(() => undefined);
  const path = `${DIR}${id}.jpg`;
  await FileSystem.copyAsync({ from: uri, to: path });
  const items = await read(userId);
  items.push({ id, caseId, path, caption, createdAt: new Date().toISOString() });
  await write(userId, items.slice(-50));
}

export async function pendingPhotos(userId: number, caseId?: number): Promise<QueuedPhoto[]> {
  const items = await read(userId);
  return caseId == null ? items : items.filter((item) => item.caseId === caseId);
}

/** Gửi các ảnh đang chờ; ảnh bị máy chủ từ chối hẳn (4xx khác 401/408/429) thì bỏ khỏi hàng. */
export async function flushPhotos(userId: number): Promise<{ sent: number; failed: number; dropped: string[] }> {
  const items = await read(userId);
  const keep: QueuedPhoto[] = [];
  const dropped: string[] = [];
  let sent = 0;
  for (const item of items) {
    try {
      await api.uploadFieldPhoto(item.caseId, item.path, item.caption, item.id);
      sent += 1;
      await FileSystem.deleteAsync(item.path, { idempotent: true });
    } catch (err) {
      const status = api.getApiErrorStatus(err);
      if (status && status >= 400 && status < 500 && ![401, 408, 429].includes(status)) {
        dropped.push(`${item.caption || "Ảnh"}: ${api.getErrorMessage(err)}`);
        await FileSystem.deleteAsync(item.path, { idempotent: true });
      } else {
        keep.push(item);
      }
    }
  }
  await write(userId, keep);
  return { sent, failed: keep.length, dropped };
}
