import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Continuous Novel Writing Studio",
  description: "Local engineering scaffold for the continuous novel writing studio.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning={true}>
      <body suppressHydrationWarning={true}>{children}</body>
    </html>
  );
}