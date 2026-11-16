"use client";

import dynamic from "next/dynamic";
import remarkGfm from "remark-gfm";

const ReactMarkdown = dynamic(() => import("react-markdown"), { ssr: false });

/**
 * Trước đây component này chỉ gắn class `prose` của @tailwindcss/typography —
 * nhưng plugin đó không có trong dự án, nên class rỗng và preflight của Tailwind
 * xoá luôn dấu đầu dòng. Hệ quả: câu trả lời của trợ lý liệt kê các bước theo
 * thứ tự nhưng trên màn hình lại thành một khối chữ không số, đúng chỗ mà kỹ
 * thuật viên cần đọc theo thứ tự nhất. Ở đây định kiểu thẳng cho từng thẻ, không
 * thêm phụ thuộc mới.
 */
const components = {
  p: (props: any) => <p className="my-2 first:mt-0 last:mb-0" {...props} />,
  ol: (props: any) => <ol className="my-2 list-decimal space-y-1 pl-5 marker:font-semibold marker:text-indigo-600" {...props} />,
  ul: (props: any) => <ul className="my-2 list-disc space-y-1 pl-5 marker:text-indigo-600" {...props} />,
  li: (props: any) => <li className="pl-1" {...props} />,
  strong: (props: any) => <strong className="font-semibold text-slate-900" {...props} />,
  em: (props: any) => <em className="text-slate-500" {...props} />,
  h1: (props: any) => <h1 className="mb-2 mt-3 text-base font-semibold text-slate-900 first:mt-0" {...props} />,
  h2: (props: any) => <h2 className="mb-2 mt-3 text-sm font-semibold text-slate-900 first:mt-0" {...props} />,
  h3: (props: any) => <h3 className="mb-1 mt-3 text-sm font-semibold text-slate-900 first:mt-0" {...props} />,
  code: (props: any) => <code className="rounded-[0.25rem] bg-slate-100 px-1 py-0.5 font-mono text-[12px]" {...props} />,
  pre: (props: any) => <pre className="my-2 overflow-x-auto rounded-lg bg-slate-900 p-3 text-[12px] text-slate-100" {...props} />,
  a: (props: any) => <a className="text-indigo-600 underline underline-offset-2" {...props} />,
  table: (props: any) => <table className="my-2 w-full border-collapse text-left text-[12px]" {...props} />,
  th: (props: any) => <th className="border-b border-slate-200 py-1 pr-3 font-semibold" {...props} />,
  td: (props: any) => <td className="border-b border-slate-100 py-1 pr-3 align-top" {...props} />,
  blockquote: (props: any) => <blockquote className="my-2 border-l-2 border-slate-200 pl-3 text-slate-500" {...props} />,
};

export default function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="max-w-none">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
