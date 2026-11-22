import * as api from "./api";
import { cacheMySchedule, getCachedMySchedule, isSecureOfflineStorageAvailable } from "./fieldOffline";

export type ScheduleResult = {
  schedule: api.MySchedule | null;
  /** Đang hiện bản đã lưu trong máy vì không có mạng. */
  offline: boolean;
  /** Thời điểm bản đã lưu được tải (chỉ khi offline). */
  cachedAt: string | null;
  error: string;
};

/**
 * M11 — tải lịch của tôi cho một ngày. Có mạng: lấy từ máy chủ rồi lưu an toàn trong máy (chỉ giữ
 * hôm nay + ngày mai). Mất mạng: trả bản đã lưu kèm thời điểm cập nhật. Dùng chung cho tab
 * "Hôm nay" và khối tóm tắt trên Tổng quan.
 */
export async function loadMySchedule(date: string): Promise<ScheduleResult> {
  const user = await api.loadUser();
  const secure = user ? await isSecureOfflineStorageAvailable() : false;
  try {
    const schedule = await api.getMySchedule(date);
    if (user && secure) {
      await cacheMySchedule(user.id, schedule, [api.localDate(0), api.localDate(1)]).catch(() => undefined);
    }
    return { schedule, offline: false, cachedAt: null, error: "" };
  } catch (error) {
    if (user && secure && api.isNetworkError(error)) {
      const cached = await getCachedMySchedule(user.id, date).catch(() => null);
      if (cached) return { schedule: cached, offline: true, cachedAt: cached.cached_at, error: "" };
      return {
        schedule: null,
        offline: true,
        cachedAt: null,
        error: "Chưa có lịch trong máy cho ngày này — cần kết nối mạng một lần.",
      };
    }
    return { schedule: null, offline: false, cachedAt: null, error: api.getErrorMessage(error) };
  }
}

export function timeOf(iso: string | null): string {
  if (!iso) return "--:--";
  const date = new Date(iso);
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

/** Mở ứng dụng bản đồ của máy tới điểm đến (ưu tiên toạ độ, thiếu thì dùng địa chỉ). */
export function directionsUrl(stop: api.MyScheduleStop): string | null {
  if (stop.latitude != null && stop.longitude != null) {
    return `https://www.google.com/maps/dir/?api=1&destination=${stop.latitude},${stop.longitude}`;
  }
  if (stop.address) return `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(stop.address)}`;
  return null;
}
