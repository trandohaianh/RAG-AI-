import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Hiển thị thời điểm từ API. SQLite trả giờ UTC không kèm múi giờ ("2026-11-16T12:55:37"), nếu để
 *  nguyên trình duyệt sẽ hiểu là giờ địa phương và lệch 7 tiếng; chuỗi không có múi giờ được coi là UTC. */
export function formatDateTime(value: string): string {
  const iso = /([zZ]|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`;
  return new Date(iso).toLocaleString("vi-VN");
}
