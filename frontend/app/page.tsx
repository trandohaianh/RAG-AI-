"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  BarChart3,
  Check,
  Code2,
  FileText,
  Gauge,
  Layers,
  Lock,
  MessageSquare,
  ScanText,
  ShieldCheck,
  Sparkles,
  ThumbsUp,
  Workflow,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import Logo, { LogoMark } from "@/components/Logo";

const TECH = [
  "Next.js 14",
  "FastAPI",
  "Google Gemini",
  "ChromaDB",
  "PostgreSQL",
  "Apache Airflow",
  "Great Expectations",
  "Docker",
];

const FEATURES = [
  {
    icon: MessageSquare,
    title: "Checklist xử lý có căn cứ",
    desc: "Gắn mỗi bước hiện trường với SOP/manual đã kiểm chứng và lưu người xác nhận, kết quả.",
    big: true,
  },
  {
    icon: ScanText,
    title: "Bàn giao ca sửa",
    desc: "Tập hợp triệu chứng, thiết bị, địa chỉ và bước tổng đài đã thử trong một hồ sơ.",
  },
  {
    icon: ThumbsUp,
    title: "Mobile cho kỹ thuật viên",
    desc: "Checklist lớn, trạng thái và nhật ký ca sửa được thiết kế cho tác nghiệp hiện trường.",
  },
  {
    icon: Layers,
    title: "Kho tri thức của đội",
    desc: "Quy trình và tài liệu hướng dẫn được lập chỉ mục trong kho dùng chung, chỉ thành viên đội truy cập.",
  },
  {
    icon: Lock,
    title: "Nhật ký theo ca",
    desc: "Ghi sự kiện, checklist và người xử lý để bàn giao, rà soát và cải thiện quy trình.",
  },
  {
    icon: Gauge,
    title: "Con người kiểm soát",
    desc: "AI chỉ truy xuất và gợi ý theo tài liệu; kỹ thuật viên xác nhận trước khi thao tác thiết bị.",
  },
];

const PIPELINE_STEPS = [
  { label: "Bronze", sub: "Metadata + hash gốc" },
  { label: "Great Expectations", sub: "Kiểm định chất lượng" },
  { label: "Silver", sub: "Chunk đã gắn cờ" },
  { label: "Gold", sub: "Sẵn sàng báo cáo" },
];

const USE_TABS = [
  { key: "chat", label: "Chat trực tiếp", icon: MessageSquare },
  { key: "ocr", label: "OCR ảnh", icon: ScanText },
  { key: "embed", label: "Nhúng website", icon: Code2 },
] as const;

const SPARK_POINTS = "0,34 12,30 24,32 36,22 48,24 60,14 72,18 84,8 96,10 108,2";

export default function HomePage() {
  const router = useRouter();
  const { user, loading } = useAuth();
  const [tab, setTab] = useState<(typeof USE_TABS)[number]["key"]>("chat");

  useEffect(() => {
    if (!loading && user) router.replace("/chat");
  }, [loading, user, router]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-muted-foreground">
        Đang tải...
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white text-slate-900">
      {/* ---------- Header ---------- */}
      <header className="sticky top-0 z-30 border-b border-slate-100 bg-white/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3.5">
          <Logo size={26} textClassName="text-sm" />
          <nav className="hidden items-center gap-7 text-[13px] font-medium text-slate-500 md:flex">
            <a href="#usage" className="transition-colors hover:text-slate-900">Cách dùng</a>
            <a href="#features" className="transition-colors hover:text-slate-900">Tính năng</a>
            <a href="#pipeline" className="transition-colors hover:text-slate-900">Data Engineering</a>
            <a href="#tech" className="transition-colors hover:text-slate-900">Công nghệ</a>
          </nav>
          <div className="flex items-center gap-1.5">
            <Button variant="ghost" size="sm" asChild>
              <Link href="/login">Đăng nhập</Link>
            </Button>
            <Button size="sm" asChild>
              <Link href="/register">Dùng thử miễn phí</Link>
            </Button>
          </div>
        </div>
      </header>

      {/* ---------- Hero ---------- */}
      <section className="relative overflow-hidden px-6 pb-16 pt-16 text-center">
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.4]"
          style={{
            backgroundImage: "radial-gradient(circle, #e2e8f0 1px, transparent 1px)",
            backgroundSize: "24px 24px",
            maskImage: "radial-gradient(ellipse 60% 50% at 50% 0%, black 40%, transparent 100%)",
          }}
        />
        <div className="relative mx-auto max-w-2xl">
          <div className="mx-auto mb-5 inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-[11px] font-medium text-slate-600">
            <Sparkles className="h-3 w-3 text-primary" />
            Mobile-first field service operations
          </div>
          <h1 className="text-3xl font-semibold leading-[1.15] tracking-tight text-slate-900 md:text-[2.75rem]">
            Từ bàn giao ca sửa
            <br />
            <span className="brand-gradient-text">đến xử lý hiện trường có bằng chứng.</span>
          </h1>
          <p className="mx-auto mt-4 max-w-lg text-[15px] leading-relaxed text-slate-500">
            Nhận ca đã được tổng đài bàn giao, đưa đúng quy trình theo model thiết bị đến điện thoại
            kỹ thuật viên, rồi lưu kết quả xử lý có trích dẫn nguồn vào kho chung của đội.
          </p>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-2.5">
            <Button className="min-w-[180px] gap-2" asChild>
              <Link href="/register">
                Khám phá FieldOps <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
            <Button variant="outline" className="min-w-[140px]" asChild>
              <Link href="/login">Đăng nhập</Link>
            </Button>
          </div>
        </div>

        {/* mock app preview */}
        <div className="relative z-10 mx-auto mt-12 max-w-3xl rounded-xl border border-slate-200 bg-white p-1.5 shadow-xl shadow-slate-200/60">
          <div className="flex items-center gap-1.5 border-b border-slate-100 px-3 py-2">
            <span className="h-2 w-2 rounded-full bg-slate-200" />
            <span className="h-2 w-2 rounded-full bg-slate-200" />
            <span className="h-2 w-2 rounded-full bg-slate-200" />
          </div>
          <div className="grid grid-cols-[160px_1fr] gap-0 text-left">
            <div className="hidden border-r border-slate-100 p-3.5 sm:block">
              <div className="brand-gradient mb-3 h-7 w-full rounded-md opacity-90" />
              <div className="space-y-1.5">
                {["Hợp đồng thuê nhà", "Báo cáo Q3", "Đặc tả hệ thống"].map((t) => (
                  <div key={t} className="truncate rounded-md bg-slate-50 px-2 py-1.5 text-[11px] text-slate-500">
                    {t}
                  </div>
                ))}
              </div>
            </div>
            <div className="p-4">
              <div className="mb-3 flex justify-end">
                <div className="brand-gradient rounded-2xl rounded-br-md px-3.5 py-2 text-[13px] text-white">
                  Điều khoản phạt trễ hạn thanh toán là gì?
                </div>
              </div>
              <div className="max-w-[85%] rounded-2xl rounded-bl-md border border-slate-100 bg-slate-50 px-3.5 py-2.5 text-[13px] text-slate-700">
                Theo Điều 8 của hợp đồng, nếu trễ hạn thanh toán quá 5 ngày, bên thuê chịu phạt
                0.05%/ngày trên số tiền chậm trả...
                <div className="mt-2 flex gap-1.5">
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
                    Bám sát nguồn 98%
                  </span>
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
                    Đúng trọng tâm 100%
                  </span>
                </div>
              </div>
            </div>
          </div>
          <p className="mt-1.5 text-center text-[10px] text-slate-400">
            Ảnh minh hoạ giao diện thực tế — số % trong ảnh là ví dụ, không phải số liệu tổng hợp.
          </p>
        </div>
      </section>

      {/* ---------- Trust stats + tech strip ---------- */}
      <section id="tech" className="border-y border-slate-100 bg-slate-50/60 py-12">
        <p className="mb-7 text-center text-[11px] font-semibold uppercase tracking-widest text-slate-400">
          Được đo kiểm thật, không phải con số quảng cáo
        </p>
        <div className="mx-auto grid max-w-3xl grid-cols-1 gap-8 px-6 text-center sm:grid-cols-3">
          {[
            { value: "3 tầng", label: "Kiểm định dữ liệu Bronze → Silver → Gold" },
            { value: "2 lớp", label: "Đánh giá độ tin cậy — người dùng + AI chấm điểm" },
            { value: "100%", label: "Câu trả lời kèm trích dẫn nguồn, không hộp đen" },
          ].map((s) => (
            <div key={s.label}>
              <p className="text-3xl font-semibold tracking-tight text-slate-900">{s.value}</p>
              <p className="mt-1 text-[13px] text-slate-500">{s.label}</p>
            </div>
          ))}
        </div>
        <p className="mx-auto mt-5 max-w-2xl text-center text-[11px] leading-relaxed text-slate-400">
          3 số trên là đặc tính kiến trúc — kiểm chứng được bằng cách đọc source code (không phải
          số đo lấy mẫu). Điểm chất lượng câu trả lời (faithfulness/relevancy) được đo trên bộ câu
          hỏi kiểm thử chạy qua đúng pipeline production, chấm chéo bằng LLM-judge và một thuật
          toán đối chiếu từ vựng độc lập không qua LLM — xem{" "}
          <code className="rounded bg-slate-100 px-1 py-0.5 text-[10px]">
            backend/scripts/run_eval_benchmark.py
          </code>{" "}
          và <code className="rounded bg-slate-100 px-1 py-0.5 text-[10px]">eval_results.json</code> để
          tái lập.
        </p>

        <p className="mb-4 mt-12 text-center text-[11px] font-semibold uppercase tracking-widest text-slate-400">
          Xây dựng trên nền tảng công nghệ đã kiểm chứng
        </p>
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-center gap-x-7 gap-y-2.5 px-6">
          {TECH.map((t) => (
            <span key={t} className="text-[13px] font-medium text-slate-400">
              {t}
            </span>
          ))}
        </div>
      </section>

      {/* ---------- Usage tabs — 1 hệ thống, nhiều cách dùng ---------- */}
      <section id="usage" className="mx-auto max-w-5xl px-6 py-20">
        <div className="mx-auto mb-10 max-w-xl text-center">
          <p className="mb-2.5 text-[13px] font-semibold text-primary">Cách dùng</p>
          <h2 className="text-2xl font-semibold tracking-tight text-slate-900 md:text-[1.9rem]">
            Một hệ thống, ba cách đưa AI đến với dữ liệu
          </h2>
        </div>

        <div className="mx-auto mb-6 flex w-fit gap-1 rounded-full border border-slate-200 bg-slate-50 p-1">
          {USE_TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-colors ${
                tab === t.key ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800"
              }`}
            >
              <t.icon className="h-3.5 w-3.5" />
              {t.label}
            </button>
          ))}
        </div>

        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          {tab === "chat" && (
            <div className="grid grid-cols-1 gap-0 sm:grid-cols-2">
              <div className="p-6">
                <p className="text-[13px] font-semibold text-slate-900">Hỏi trực tiếp trong app</p>
                <p className="mt-1.5 text-[13px] leading-relaxed text-slate-500">
                  Tải tài liệu lên, đặt câu hỏi bằng ngôn ngữ tự nhiên — nhận câu trả lời kèm trích
                  dẫn nguồn và điểm tin cậy sau mỗi lượt.
                </p>
              </div>
              <div className="border-t border-slate-100 bg-slate-50/60 p-4 sm:border-l sm:border-t-0">
                <div className="mb-2 flex justify-end">
                  <div className="brand-gradient rounded-xl rounded-br-sm px-3 py-1.5 text-[12px] text-white">
                    Hợp đồng có điều khoản phạt gì?
                  </div>
                </div>
                <div className="max-w-[90%] rounded-xl rounded-bl-sm border border-slate-200 bg-white px-3 py-2 text-[12px] text-slate-700">
                  Điều 8: trễ hạn quá 5 ngày, phạt 0.05%/ngày...
                </div>
              </div>
            </div>
          )}
          {tab === "ocr" && (
            <div className="grid grid-cols-1 gap-0 sm:grid-cols-2">
              <div className="p-6">
                <p className="text-[13px] font-semibold text-slate-900">OCR ảnh chụp tài liệu</p>
                <p className="mt-1.5 text-[13px] leading-relaxed text-slate-500">
                  Không có bản PDF? Chụp ảnh cũng được — Gemini Vision đọc chữ trong ảnh rồi lập
                  chỉ mục như văn bản thường, hỏi được ngay sau đó.
                </p>
              </div>
              <div className="border-t border-slate-100 bg-slate-50/60 p-4 sm:border-l sm:border-t-0">
                <div className="rounded-lg border border-dashed border-slate-300 bg-white p-4 text-center">
                  <ScanText className="mx-auto mb-1.5 h-5 w-5 text-slate-400" />
                  <p className="text-[11px] text-slate-400">bien-ban-hop.jpg → đang OCR...</p>
                </div>
                <p className="mt-2 text-[11px] text-emerald-600">✓ 12 chunks đã lập chỉ mục</p>
              </div>
            </div>
          )}
          {tab === "embed" && (
            <div className="grid grid-cols-1 gap-0 sm:grid-cols-2">
              <div className="p-6">
                <p className="text-[13px] font-semibold text-slate-900">Nhúng vào website khác</p>
                <p className="mt-1.5 text-[13px] leading-relaxed text-slate-500">
                  Cấp API key hoặc dán 1 thẻ iframe — khung chat của bạn xuất hiện ngay trên website
                  công ty, dùng chung kho tài liệu đã tải lên.
                </p>
              </div>
              <div className="border-t border-slate-100 bg-slate-50/60 p-4 sm:border-l sm:border-t-0">
                <div className="relative h-28 rounded-lg border border-slate-200 bg-white p-2">
                  <div className="h-2 w-16 rounded bg-slate-100" />
                  <div className="mt-1.5 h-2 w-24 rounded bg-slate-100" />
                  <div className="absolute bottom-2 right-2 flex h-9 w-9 items-center justify-center rounded-full bg-slate-900 shadow-lg">
                    <MessageSquare className="h-4 w-4 text-white" />
                  </div>
                </div>
                <p className="mt-2 text-[11px] text-slate-400">trang-web-cua-ban.com</p>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* ---------- Feature bento grid ---------- */}
      <section id="features" className="mx-auto max-w-6xl px-6 py-20">
        <div className="mx-auto mb-12 max-w-2xl text-center">
          <p className="mb-2.5 text-[13px] font-semibold text-primary">Tính năng</p>
          <h2 className="text-2xl font-semibold tracking-tight text-slate-900 md:text-[1.9rem]">
            Một vòng vận hành khép kín cho dịch vụ hiện trường
          </h2>
        </div>

        <div className="grid grid-cols-1 gap-3.5 md:grid-cols-3">
          {FEATURES.map((f) => (
            <div
              key={f.title}
              className={`rounded-xl border border-slate-200 bg-white p-5 transition-shadow hover:shadow-md hover:shadow-slate-200/60 ${
                f.big ? "md:col-span-2" : ""
              }`}
            >
              <div className="brand-gradient mb-3.5 flex h-9 w-9 items-center justify-center rounded-lg">
                <f.icon className="h-4 w-4 text-white" />
              </div>
              <h3 className="text-[14px] font-semibold text-slate-900">{f.title}</h3>
              <p className="mt-1.5 text-[13px] leading-relaxed text-slate-500">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ---------- Dark Data Engineering / Audit trail section ---------- */}
      <section id="pipeline" className="bg-[#0a0a0f] px-6 py-20 text-white">
        <div className="mx-auto max-w-5xl">
          <div className="mb-12 text-center">
            <p className="mb-2.5 text-[13px] font-semibold text-violet-400">Data Engineering</p>
            <h2 className="text-2xl font-semibold tracking-tight md:text-[1.9rem]">
              Không chỉ là chatbot — là cả một pipeline dữ liệu có thể audit
            </h2>
            <p className="mx-auto mt-3 max-w-xl text-[13px] leading-relaxed text-white/50">
              Mỗi tài liệu tải lên đi qua kiến trúc Medallion đầy đủ, kiểm định chất lượng và được
              điều phối tự động. Khác với hộp đen của các công cụ chat-với-tài-liệu thông thường,
              mọi câu trả lời đều truy ngược được về trạng thái kiểm định thật của nguồn.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            {PIPELINE_STEPS.map((s, i) => (
              <div key={s.label} className="relative rounded-lg border border-white/10 bg-white/[0.03] p-3.5">
                <span className="text-[11px] font-mono text-white/30">0{i + 1}</span>
                <p className="mt-1.5 text-[13px] font-semibold">{s.label}</p>
                <p className="mt-0.5 text-[11px] text-white/40">{s.sub}</p>
              </div>
            ))}
          </div>

          <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-[1fr_1fr]">
            {/* Analytics mockup — số liệu ví dụ ở quy mô thật của 1 tài khoản demo */}
            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-5">
              <p className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-white/40">
                Trang Thống kê — giám sát tự động
              </p>
              <div className="flex items-end justify-between">
                <div>
                  <p className="text-2xl font-semibold">98%</p>
                  <p className="text-[11px] text-white/40">Bám sát nguồn TB (7 ngày)</p>
                </div>
                <svg width="110" height="36" viewBox="0 0 110 36" fill="none" className="text-violet-400">
                  <polyline points={SPARK_POINTS} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <div className="mt-4 grid grid-cols-3 gap-2 border-t border-white/10 pt-4 text-center">
                {[
                  { icon: Workflow, title: "Airflow", desc: "Ingest hằng ngày" },
                  { icon: ShieldCheck, title: "GE", desc: "Gắn cờ từng chunk" },
                  { icon: BarChart3, title: "Grafana", desc: "Giám sát real-time" },
                ].map((f) => (
                  <div key={f.title}>
                    <f.icon className="mx-auto mb-1.5 h-4 w-4 text-violet-400" />
                    <p className="text-[11px] font-semibold">{f.title}</p>
                    <p className="text-[10px] text-white/40">{f.desc}</p>
                  </div>
                ))}
              </div>
              <p className="mt-3 text-[10px] text-white/30">
                Ảnh minh hoạ layout trang Thống kê thật trong app — số ví dụ ở quy mô 1 tài khoản.
              </p>
            </div>

            {/* Audit-trail mockup — chứng minh trực quan, không chỉ mô tả bằng chữ */}
            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-5">
              <p className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-white/40">
                Xem trước — mỗi câu trả lời đều kèm bằng chứng
              </p>
              <div className="rounded-lg border border-white/10 bg-black/40 p-3.5">
                <p className="text-[13px] leading-relaxed text-white/80">
                  Theo Điều 8 của hợp đồng, nếu trễ hạn thanh toán quá 5 ngày, bên thuê chịu phạt
                  0.05%/ngày...
                </p>
                <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                  <span className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                    Bám sát nguồn 98%
                  </span>
                  <span className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                    Đúng trọng tâm 100%
                  </span>
                </div>
                <div className="mt-2.5 flex items-center gap-1.5 border-t border-white/10 pt-2.5">
                  <span className="text-[11px] text-white/40">Nguồn: Hợp đồng thuê nhà.pdf</span>
                  <span className="inline-flex items-center gap-1 rounded-full border border-violet-400/30 bg-violet-400/10 px-2 py-0.5 text-[10px] font-medium text-violet-300">
                    <ShieldCheck className="h-3 w-3" />
                    Đã kiểm định · Gold
                  </span>
                </div>
              </div>
              <p className="mt-3 text-[10px] text-white/30">
                Ảnh minh hoạ giao diện (số % là ví dụ). Badge "Đã kiểm định · Gold" đọc trực tiếp
                cột <code className="rounded bg-white/10 px-1">validation_passed</code> thật trong
                Postgres khi bật Data Pipeline — xem endpoint{" "}
                <code className="rounded bg-white/10 px-1">GET /documents/lineage</code>.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ---------- Multi-tenant / trust section ---------- */}
      <section className="mx-auto max-w-5xl px-6 py-20">
        <div className="grid items-center gap-10 md:grid-cols-2">
          <div>
            <p className="mb-2.5 text-[13px] font-semibold text-primary">Bảo mật</p>
            <h2 className="text-2xl font-semibold tracking-tight text-slate-900">
              Dữ liệu của bạn, chỉ mình bạn thấy
            </h2>
            <ul className="mt-5 space-y-2.5">
              {[
                "Mật khẩu băm bằng bcrypt, không lưu dạng thô",
                "Xác thực JWT, mỗi request tự kiểm tra quyền",
                "Mỗi user có kho vector ChromaDB tách biệt hoàn toàn",
                "API key riêng cho tích hợp bên ngoài, độc lập với tài khoản",
              ].map((t) => (
                <li key={t} className="flex items-start gap-2.5 text-[13px] text-slate-600">
                  <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
                  {t}
                </li>
              ))}
            </ul>
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-5">
            <div className="flex items-center gap-3">
              <div className="brand-gradient flex h-9 w-9 items-center justify-center rounded-full text-[13px] font-bold text-white">
                A
              </div>
              <div>
                <p className="text-[13px] font-semibold text-slate-800">User A</p>
                <p className="text-[11px] text-slate-400">collection: user_1</p>
              </div>
            </div>
            <div className="my-3.5 border-t border-dashed border-slate-200" />
            <div className="flex items-center gap-3 opacity-40">
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-slate-300 text-[13px] font-bold text-white">
                B
              </div>
              <div>
                <p className="text-[13px] font-semibold text-slate-500">User B</p>
                <p className="text-[11px] text-slate-400">collection: user_2 · không nhìn thấy dữ liệu A</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ---------- Final CTA ---------- */}
      <section className="bg-[#0a0a0f] px-6 py-20 text-white">
        <div className="mx-auto flex max-w-4xl flex-col items-center gap-5 text-center">
          <h2 className="text-2xl font-semibold tracking-tight md:text-[1.9rem]">
            Sẵn sàng để tài liệu tự trả lời câu hỏi?
          </h2>
          <p className="max-w-md text-[13px] text-white/50">
            Đăng ký miễn phí — tài khoản đầu tiên là admin. Không cần thẻ, không cần cài đặt gì thêm.
          </p>
          <Button variant="secondary" className="min-w-[200px] shrink-0" asChild>
            <Link href="/register">Đăng ký ngay</Link>
          </Button>
        </div>
      </section>

      {/* ---------- Footer ---------- */}
      <footer className="border-t border-slate-100 px-6 py-9">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 sm:flex-row">
          <div className="flex items-center gap-2 text-[13px] text-slate-400">
            <LogoMark size={18} />© {new Date().getFullYear()} FieldOps
          </div>
          <div className="flex items-center gap-2 text-[11px] text-slate-400">
            <FileText className="h-3.5 w-3.5" />
            Đồ án hệ thống RAG + Data Engineering
          </div>
        </div>
      </footer>
    </div>
  );
}
