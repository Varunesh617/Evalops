import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/Sidebar";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "EvalOps — Pipeline Evaluation & Optimization",
  description: "Unified full-pipeline evaluation and optimization platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased dark`}
    >
      <body className="min-h-full flex">
        <Sidebar />
        <div className="flex-1 flex flex-col min-h-screen overflow-hidden">
          <header className="flex items-center justify-between px-6 py-3 border-b border-zinc-200 dark:border-zinc-800 bg-white/80 dark:bg-zinc-950/80 backdrop-blur-sm">
            <div className="flex items-center gap-3">
              <span className="lg:hidden text-xl font-bold text-zinc-900 dark:text-white">EvalOps</span>
              <h1 className="text-sm font-medium text-zinc-500 dark:text-zinc-400">Pipeline Intelligence</h1>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs text-zinc-400">localhost:8000</span>
              <div className="w-2 h-2 rounded-full bg-emerald-500" title="Connected" />
            </div>
          </header>
          <main className="flex-1 overflow-y-auto bg-zinc-50 dark:bg-zinc-950">
            <div className="p-6">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
