/**
 * M15 — đọc dữ liệu kèm thông báo đẩy. Máy chủ chỉ gửi {"screen":"today","date":"YYYY-MM-DD"|null}
 * (không mã ca, không thông tin khách). Mọi dạng khác bị bỏ qua.
 */
export function parsePushTarget(data: unknown, today: string, tomorrow: string): { day: 0 | 1 } | null {
  if (!data || typeof data !== "object") return null;
  const value = data as Record<string, unknown>;
  if (value.screen !== "today") return null;
  // Tab "Hôm nay" chỉ có hôm nay / ngày mai; ngày khác thì mở hôm nay.
  return { day: value.date === tomorrow && tomorrow !== today ? 1 : 0 };
}
