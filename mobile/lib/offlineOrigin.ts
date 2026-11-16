import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

const ORIGIN_KEY = "fieldops.offline.v1.api_origin";
let bindingLock: Promise<unknown> = Promise.resolve();

function originOf(apiUrl: string): string {
  return new URL(apiUrl).origin;
}

// Bind the existing offline namespace before API settings change. The records
// remain untouched when another server is selected and can be recovered by
// selecting their original server again.
export function rememberOfflineApiOrigin(apiUrl: string): Promise<string> {
  const origin = originOf(apiUrl);
  const run = bindingLock.then(async () => {
    if (Platform.OS === "web" || !(await SecureStore.isAvailableAsync())) return origin;
    const existing = await SecureStore.getItemAsync(ORIGIN_KEY);
    if (existing) return existing;
    await SecureStore.setItemAsync(ORIGIN_KEY, origin);
    return origin;
  });
  bindingLock = run.catch(() => undefined);
  return run;
}

export async function assertOfflineApiOrigin(apiUrl: string): Promise<void> {
  if (await rememberOfflineApiOrigin(apiUrl) !== originOf(apiUrl)) {
    throw new Error("Dữ liệu offline thuộc máy chủ khác. Chọn lại máy chủ gốc để xem và đồng bộ; thao tác cũ vẫn được giữ trên thiết bị.");
  }
}
