"use client";
import { useEffect, useRef } from "react";

/** Một nét ký: danh sách điểm [x, y] chuẩn hoá 0..1 theo khung 3:1 (khớp máy chủ, M12). */
export type Stroke = [number, number][];

const WIDTH = 900;
const HEIGHT = 300;

/**
 * Khung ký bằng chuột / bút / ngón tay (Pointer Events), không cần thư viện. Chỉ ghi lại nét;
 * máy chủ tự vẽ thành ảnh chữ ký.
 */
export default function SignatureCanvas({ strokes, onChange }: { strokes: Stroke[]; onChange: (s: Stroke[]) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawing = useRef<Stroke | null>(null);

  useEffect(() => {
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, WIDTH, HEIGHT);
    ctx.lineWidth = 5;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#111827";
    for (const stroke of strokes) {
      ctx.beginPath();
      stroke.forEach(([x, y], i) => (i ? ctx.lineTo(x * WIDTH, y * HEIGHT) : ctx.moveTo(x * WIDTH, y * HEIGHT)));
      ctx.stroke();
    }
  }, [strokes]);

  function point(event: React.PointerEvent<HTMLCanvasElement>): [number, number] {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1);
    const y = Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1);
    return [Number(x.toFixed(4)), Number(y.toFixed(4))];
  }

  return (
    <canvas
      ref={canvasRef}
      width={WIDTH}
      height={HEIGHT}
      aria-label="Khung ký của khách hàng"
      className="w-full touch-none rounded-lg border-2 border-dashed border-slate-300 bg-white"
      style={{ aspectRatio: "3 / 1" }}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        drawing.current = [point(event)];
        onChange([...strokes, drawing.current]);
      }}
      onPointerMove={(event) => {
        if (!drawing.current) return;
        drawing.current.push(point(event));
        onChange([...strokes.slice(0, -1), [...drawing.current]]);
      }}
      onPointerUp={() => {
        drawing.current = null;
      }}
      onPointerCancel={() => {
        drawing.current = null;
      }}
    />
  );
}
