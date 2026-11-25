import { Appearance } from "react-native";

/* Bảng màu FieldOps — "cam an toàn + xám thép" (đồng bộ với web).
   Cam đậm cho nút (chữ trắng đạt 5,2:1 ở cả hai chế độ); cam sáng chỉ dùng cho chữ/điểm nhấn trên nền tối.
   Đỏ/vàng/xanh lá chỉ dùng cho trạng thái. App theo chế độ sáng/tối của điện thoại khi mở. */
const LIGHT = {
  background: "#F5F6F8",
  card: "#FFFFFF",
  text: "#0F172A",
  textMuted: "#5B6475",
  border: "#E2E6EC",
  primary: "#C2410C",
  primaryTo: "#9A3412",
  primaryText: "#C2410C",
  primarySoft: "#FFF1E7",
  primaryDeep: "#7C2D12",
  primaryForeground: "#FFFFFF",
  secondary: "#EEF1F5",
  success: "#15803D",
  successSoft: "#E7F5EC",
  destructive: "#DC2626",
  dangerSoft: "#FEF2F2",
  dangerBorder: "#FECACA",
  warning: "#B45309",
  warningSoft: "#FFF7E6",
  warningText: "#92400E",
  heroBg: "#1C2633",
  heroLine: "#334155",
  heroText: "#EEF1F5",
  heroMuted: "#C3CBD8",
  heroAccent: "#FFB37A",
};

const DARK: typeof LIGHT = {
  background: "#111318",
  card: "#1A1D24",
  text: "#E7E9EE",
  textMuted: "#9AA3B2",
  border: "#2C313C",
  primary: "#C2410C",
  primaryTo: "#9A3412",
  primaryText: "#FF8A4C",
  primarySoft: "#3A2216",
  primaryDeep: "#FFC9A6",
  primaryForeground: "#FFFFFF",
  secondary: "#242934",
  success: "#6BD69A",
  successSoft: "#12301F",
  destructive: "#FF8A80",
  dangerSoft: "#3A1715",
  dangerBorder: "#7A2E2A",
  warning: "#F5B54A",
  warningSoft: "#3A2A10",
  warningText: "#FCD58A",
  heroBg: "#0B0E13",
  heroLine: "#2C313C",
  heroText: "#EEF1F5",
  heroMuted: "#B4BCC9",
  heroAccent: "#FF9B63",
};

export const scheme: "light" | "dark" = Appearance.getColorScheme() === "dark" ? "dark" : "light";
export const colors = scheme === "dark" ? DARK : LIGHT;

export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  full: 999,
};
