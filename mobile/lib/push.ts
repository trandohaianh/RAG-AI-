/**
 * M15 — thông báo đẩy (docs/design/M15_thong_bao_day.md). Chỉ Android ở giai đoạn 1 (Q3).
 *
 * - Không tự bật hộp xin quyền: thợ bấm "Bật" trên thẻ ở tab Hôm nay.
 * - Đã có quyền thì mỗi lần mở tab Hôm nay gửi lại token cho người đang đăng nhập.
 * - Đăng xuất thì xoá token trên máy chủ TRƯỚC khi xoá phiên.
 */
import { Platform } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import * as api from "./api";

const STORED_TOKEN = "fieldops.push_token";
export const CHANNEL_ID = "schedule";

export type PushState = "unsupported" | "off" | "denied" | "on";

function projectId(): string | null {
  const extra = Constants.expoConfig?.extra as { eas?: { projectId?: string } } | undefined;
  return extra?.eas?.projectId ?? Constants.easConfig?.projectId ?? null;
}

/** Máy thật chạy Android, bản build có projectId của Expo. Bản web / máy giả lập thì không. */
export function pushSupported(): boolean {
  return Platform.OS === "android" && Device.isDevice && !!projectId();
}

let configured = false;
export async function configurePush(): Promise<void> {
  if (configured || Platform.OS !== "android") return;
  configured = true;
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  });
  await Notifications.setNotificationChannelAsync(CHANNEL_ID, {
    name: "Lịch làm việc",
    importance: Notifications.AndroidImportance.HIGH,
    sound: "default",
  });
}

async function register(): Promise<void> {
  const id = projectId();
  if (!id) return;
  const token = (await Notifications.getExpoPushTokenAsync({ projectId: id })).data;
  await api.registerPushDevice(token);
  await AsyncStorage.setItem(STORED_TOKEN, token);
}

/** Trạng thái để hiện thẻ ở tab Hôm nay. Đã có quyền thì đăng ký lại (nhẹ, idempotent). */
export async function getPushState(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  try {
    if (!(await api.getPushStatus()).enabled) return "unsupported";
  } catch {
    return "unsupported"; // offline: không hiện thẻ, lần sau hỏi lại
  }
  const permission = await Notifications.getPermissionsAsync();
  if (permission.granted) {
    await configurePush();
    await register().catch(() => undefined);
    return "on";
  }
  return permission.canAskAgain ? "off" : "denied";
}

/** Thợ bấm "Bật thông báo". */
export async function enablePush(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  await configurePush();
  const permission = await Notifications.requestPermissionsAsync();
  if (!permission.granted) return permission.canAskAgain ? "off" : "denied";
  await register();
  return "on";
}

/** Gọi khi đăng xuất. Lỗi mạng không chặn đăng xuất (máy chủ tự dọn token hỏng sau). */
export async function unregisterPush(): Promise<void> {
  const token = await AsyncStorage.getItem(STORED_TOKEN).catch(() => null);
  if (!token) return;
  try {
    await Promise.race([
      api.unregisterPushDevice(token),
      new Promise((resolve) => setTimeout(resolve, 4000)),
    ]);
  } catch {
    /* mất mạng: vẫn đăng xuất */
  }
  await AsyncStorage.removeItem(STORED_TOKEN).catch(() => undefined);
}
