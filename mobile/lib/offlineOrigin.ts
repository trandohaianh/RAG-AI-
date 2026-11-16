import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

const ORIGIN_KEY = "fieldops.offline.v1.api_origin";
// Ghi cùng lúc với bản ghi offline đầu tiên. Chỉ khi có dữ liệu thật mới cần khoá vùng
// offline vào một máy chủ; trước đây chỉ cần ĐỌC (hoặc đổi máy chủ) là đã khoá vĩnh viễn vào
// origin đầu tiên — kể cả địa chỉ dev mặc định — làm mất chế độ offline trên máy chủ thật.
const MARKER_KEY = "fieldops.offline.v1.has_records";
let bindingLock: Promise<unknown> = Promise.resolve();

function originOf(apiUrl: string): string {
  return new URL(apiUrl).origin;
}

function withBindingLock<T>(task: () => Promise<T>): Promise<T> {
  const run = bindingLock.then(task, task);
  bindingLock = run.catch(() => undefined);
  return run;
}

async function secureAvailable(): Promise<boolean> {
  return Platform.OS !== "web" && (await SecureStore.isAvailableAsync());
}

/** Gọi trước khi đổi máy chủ API: nếu thiết bị đang giữ dữ liệu offline mà chưa gắn origin
 * (dữ liệu từ bản cũ), gắn nó với máy chủ cũ để dữ liệu không lộ sang máy chủ mới. Không có
 * dữ liệu thì không gắn gì cả. Trả về origin đang gắn (hoặc null). */
export function rememberOfflineApiOrigin(apiUrl: string): Promise<string | null> {
  const origin = originOf(apiUrl);
  return withBindingLock(async () => {
    if (!(await secureAvailable())) return null;
    const existing = await SecureStore.getItemAsync(ORIGIN_KEY);
    if (existing) return existing;
    if (!(await SecureStore.getItemAsync(MARKER_KEY))) return null;
    await SecureStore.setItemAsync(ORIGIN_KEY, origin);
    return origin;
  });
}

/** Trước khi GHI dữ liệu offline: gắn vùng offline với máy chủ hiện tại (lần đầu), hoặc từ
 * chối nếu vùng này thuộc máy chủ khác. */
export function bindOfflineApiOriginForWrite(apiUrl: string): Promise<void> {
  const origin = originOf(apiUrl);
  return withBindingLock(async () => {
    if (!(await secureAvailable())) return;
    const existing = await SecureStore.getItemAsync(ORIGIN_KEY);
    if (existing && existing !== origin) throw new Error(OTHER_SERVER);
    if (!existing) await SecureStore.setItemAsync(ORIGIN_KEY, origin);
    if (!(await SecureStore.getItemAsync(MARKER_KEY))) await SecureStore.setItemAsync(MARKER_KEY, "1");
  });
}

const OTHER_SERVER =
  "Dữ liệu offline thuộc máy chủ khác. Chọn lại máy chủ gốc để xem và đồng bộ; thao tác cũ vẫn được giữ trên thiết bị.";

/** Trước khi ĐỌC: chỉ từ chối khi vùng offline đã gắn với máy chủ khác. Chưa gắn nghĩa là
 * chưa có dữ liệu nào để lộ, và việc đọc không được tạo ra ràng buộc. */
export async function assertOfflineApiOrigin(apiUrl: string): Promise<void> {
  const origin = originOf(apiUrl);
  const existing = await withBindingLock(async () =>
    (await secureAvailable()) ? SecureStore.getItemAsync(ORIGIN_KEY) : null
  );
  if (existing && existing !== origin) throw new Error(OTHER_SERVER);
}
