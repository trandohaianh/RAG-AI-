import Link from "next/link";
import { POLICY_SECTIONS, PRIVACY_CONTACT, PRIVACY_VERSION } from "@/lib/privacy";

export const metadata = { title: "Chính sách quyền riêng tư — FieldOps" };

/** Trang công khai: xem được cả khi chưa đăng nhập (dẫn từ màn đăng ký). */
export default function PrivacyPage() {
  return (
    <main className="min-h-screen bg-slate-50 px-4 py-10">
      <article className="mx-auto max-w-2xl rounded-xl border border-slate-200 bg-white p-6 shadow-xs md:p-8">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Chính sách quyền riêng tư</h1>
        <p className="mt-1 text-sm text-slate-500">
          FieldOps · phiên bản {PRIVACY_VERSION}. Áp dụng theo Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15.
        </p>
        <p className="mt-4 text-sm text-slate-700">
          FieldOps giúp đội kỹ thuật nhận ca, xếp lịch và ghi nhận công việc tại nhà khách. Trang này giải thích dữ liệu
          nào được thu thập, dùng để làm gì, gửi cho ai và bạn có những quyền gì.
        </p>
        {POLICY_SECTIONS.map((section) => (
          <section key={section.title} className="mt-6">
            <h2 className="text-base font-semibold text-slate-900">{section.title}</h2>
            <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-slate-700">
              {section.items.map((item) => (
                <li key={item}>{item.replace("{contact}", PRIVACY_CONTACT)}</li>
              ))}
            </ul>
          </section>
        ))}
        <p className="mt-8 text-sm">
          <Link href="/register" className="font-medium text-primary hover:underline">
            ← Quay lại đăng ký
          </Link>
        </p>
      </article>
    </main>
  );
}
