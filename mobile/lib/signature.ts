import type { SignatureStroke } from "./api";

/** Giới hạn khớp máy chủ (backend/field/closures.py: tối đa 4000 điểm, 60 nét). */
export const MAX_SIGNATURE_POINTS = 1500;
export const MAX_SIGNATURE_STROKES = 60;

/** Thêm một điểm (toạ độ pixel trong khung) vào nét ký, chuẩn hoá về 0..1 và bỏ điểm trùng. */
export function addSignaturePoint(
  stroke: SignatureStroke,
  x: number,
  y: number,
  width: number,
  height: number
): SignatureStroke {
  const clamp = (value: number) => Math.min(Math.max(value, 0), 1);
  const point: [number, number] = [
    Number(clamp(x / Math.max(width, 1)).toFixed(4)),
    Number(clamp(y / Math.max(height, 1)).toFixed(4)),
  ];
  const last = stroke[stroke.length - 1];
  if (last && last[0] === point[0] && last[1] === point[1]) return stroke;
  return [...stroke, point];
}

/** Đổi nét ký chuẩn hoá thành đường SVG trong khung width×height. */
export function strokeToPath(stroke: SignatureStroke, width: number, height: number): string {
  if (stroke.length === 0) return "";
  const [first, ...rest] = stroke.map(([x, y]) => `${(x * width).toFixed(1)} ${(y * height).toFixed(1)}`);
  // Một chấm vẫn hiện được: vẽ đoạn rất ngắn tại chỗ.
  return rest.length ? `M${first} L${rest.join(" L")}` : `M${first} l0.1 0`;
}

/** Chuẩn bị nét ký để gửi: bỏ nét rỗng, giới hạn số nét và tổng số điểm. */
export function compactSignature(strokes: SignatureStroke[]): SignatureStroke[] {
  const out: SignatureStroke[] = [];
  let budget = MAX_SIGNATURE_POINTS;
  for (const stroke of strokes.filter((s) => s.length > 0).slice(0, MAX_SIGNATURE_STROKES)) {
    if (budget <= 0) break;
    // Nét quá dài: lấy mẫu đều để vừa ngân sách điểm, giữ điểm đầu và cuối.
    const take = Math.min(stroke.length, budget);
    const step = stroke.length / take;
    const sampled = take === stroke.length
      ? stroke
      : Array.from({ length: take }, (_, i) => stroke[Math.min(stroke.length - 1, Math.round(i * step))]);
    sampled[sampled.length - 1] = stroke[stroke.length - 1];
    out.push(sampled);
    budget -= sampled.length;
  }
  return out;
}
