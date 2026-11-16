import type { Metadata } from "next";
import { connection } from "next/server";
import { Inter } from "next/font/google";
import { Providers } from "@/components/providers";
import "./globals.css";

const inter = Inter({ subsets: ["latin", "vietnamese"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "FieldOps",
  description: "Field service operations, shared technical knowledge, and grounded AI assistance.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Render theo từng request: nonce CSP (proxy.ts) chỉ gắn được vào script khi trang không
  // được dựng sẵn lúc build.
  await connection();
  return (
    <html lang="vi" suppressHydrationWarning>
      <body className={`${inter.variable} font-sans antialiased`} suppressHydrationWarning>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
