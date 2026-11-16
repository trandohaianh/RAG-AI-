"""Sinh quy tắc .dark cho các class màu Tailwind thực sự dùng trong mã (FieldOps web)."""
import re, sys, pathlib
root = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[2])
SRC = [p for d in ("app", "components") for p in (root / d).rglob("*.tsx")]
TOK = re.compile(r"((?:[a-z-]+:)*)(bg|text|border|divide|ring|placeholder)-(white|slate|gray|amber|emerald|red|rose)(?:-(\d{2,3}))?(?:/(\d+))?(?![\w-])")
SURF = {"white": "#181b22", "50": "#1e222a", "100": "#242934", "200": "#2c313c", "300": "#3b414e"}
TEXT_SLATE = {"950": "#f7f8fa", "900": "#eef1f5", "800": "#e1e5ec", "700": "#cdd3dd", "600": "#b4bcc9", "500": "#9aa3b2", "400": "#7c8596"}
LINE = {"white": "#2c313c", "50": "#242934", "100": "#242934", "200": "#2c313c", "300": "#3b414e"}
STATUS = {  # (nền 50, nền 100, chữ đậm, viền)
    "amber": ("#3a2a10", "#4a3612", "#fcd58a", "#7a5a1e"), "emerald": ("#12301f", "#173d27", "#6bd69a", "#2e6b47"),
    "red": ("#3a1715", "#4a1d1a", "#ff8a80", "#7a2e2a"), "rose": ("#3a1520", "#4a1b28", "#ff8fa3", "#7a2a3a")}
MEDIA = {"sm": "40rem", "md": "48rem", "lg": "64rem", "xl": "80rem", "2xl": "96rem"}
STATE = {"hover": ":hover", "focus": ":focus", "focus-visible": ":focus-visible", "active": ":active", "disabled": ":disabled"}


def color(prop, fam, shade):
    if fam == "white":
        return SURF["white"] if prop == "bg" else (LINE["white"] if prop in ("border", "divide", "ring") else None)
    if fam in ("slate", "gray"):
        if prop == "bg":
            return SURF.get(shade)
        if prop in ("border", "divide", "ring"):
            return LINE.get(shade)
        if prop in ("text", "placeholder"):
            return TEXT_SLATE.get(shade)
    if fam in STATUS:
        bg50, bg100, txt, line = STATUS[fam]
        if prop == "bg":
            return {"50": bg50, "100": bg100}.get(shade)
        if prop == "text" and shade in ("600", "700", "800", "900"):
            return txt
        if prop in ("border", "ring") and shade in ("100", "200", "300"):
            return line
    return None


def rgba(hex_, alpha):
    h = hex_.lstrip("#")
    return f"rgb({int(h[0:2],16)} {int(h[2:4],16)} {int(h[4:6],16)} / {alpha}%)"


rules, seen = [], set()
for f in SRC:
    for m in TOK.finditer(f.read_text(encoding="utf-8")):
        variants, prop, fam, shade, alpha = m.group(1), m.group(2), m.group(3), m.group(4) or "", m.group(5)
        cls = m.group(0)
        if cls in seen:
            continue
        seen.add(cls)
        if fam == "white" and alpha and int(alpha) < 50:
            continue                                  # lớp phủ mờ trên nền tối: giữ nguyên
        c = color(prop, fam, shade if fam != "white" else "white")
        if not c:
            continue
        val = rgba(c, alpha) if alpha else c
        esc = re.sub(r"([:/.\[\]])", r"\\\1", cls)
        vs = [v for v in variants.split(":") if v]
        media = next((MEDIA[v] for v in vs if v in MEDIA), None)
        states = "".join(STATE.get(v, "") for v in vs if v in STATE)
        group = any(v == "group-hover" for v in vs)
        sel = (f".dark .group:hover .{esc}" if group else f".dark .{esc}{states}")
        decl = {"bg": "background-color", "text": "color", "placeholder": "color", "border": "border-color",
                "divide": "border-color", "ring": "--tw-ring-color"}[prop]
        if prop == "divide":
            sel = sel + " > :not(:last-child)"
        if prop == "placeholder":
            sel = sel + "::placeholder"
        rule = f"{sel}{{{decl}:{val}}}"
        rules.append(f"@media (min-width:{media}){{{rule}}}" if media else rule)
header = "/* SINH TỰ ĐỘNG bởi scripts/gen_dark_theme.py (python scripts/gen_dark_theme.py . app/theme-dark.css) — đừng sửa tay. Ánh xạ class màu cứng sang bảng màu tối. */\n"
out.write_text(header + "\n".join(sorted(rules)) + "\n", encoding="utf-8")
print(len(rules), "rules")
