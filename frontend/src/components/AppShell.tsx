"use client";

import { useEffect, useState, type ReactNode } from "react";
import Sidebar from "@/components/Sidebar";
import useHealthCheck from "@/hooks/useHealthCheck";

function ConnectionIndicator() {
  const { status, checkNow } = useHealthCheck();
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    setNow(Date.now());
    const interval = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(interval);
  }, []);

  const getStatusColor = () => {
    if (!status.connected) return "bg-red-500";
    if (status.failureCount > 0) return "bg-yellow-500";
    return "bg-emerald-500";
  };

  const getStatusText = () => {
    if (!status.connected) return "Disconnected";
    if (status.failureCount > 0) return "Degraded";
    return "Connected";
  };

  const formatLastCheck = () => {
    if (!status.lastCheck || now === null) return "Never";
    const date = new Date(status.lastCheck);
    const diff = now - date.getTime();
    if (diff < 60000) return "Just now";
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    return `${Math.floor(diff / 3600000)}h ago`;
  };

  return (
    <button
      onClick={checkNow}
      className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-zinc-100/50 dark:bg-zinc-800/50 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer group"
      title="Click to refresh health status"
    >
      <span className="text-xs text-zinc-400">localhost:8000</span>
      <div
        className={`w-2 h-2 rounded-full ${getStatusColor()} group-hover:scale-110 transition-transform`}
        title={getStatusText()}
      />
      <span
        className={`text-xs hidden sm:block ${
          status.connected
            ? "text-emerald-600 dark:text-emerald-400"
            : status.failureCount > 0
              ? "text-yellow-600 dark:text-yellow-400"
              : "text-red-600 dark:text-red-400"
        }`}
      >
        {getStatusText()}
      </span>
      <span className="text-xs text-zinc-400 hidden lg:block">({formatLastCheck()})</span>
    </button>
  );
}

function MobileSidebar({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  return (
    <>
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <aside
        className={`fixed top-0 left-0 z-50 h-full w-64 bg-zinc-900 text-zinc-300 border-r border-zinc-800 transform transition-transform duration-300 ease-in-out lg:hidden ${
          isOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between px-6 py-5 border-b border-zinc-800">
          <span className="text-xl font-bold text-white tracking-tight">EvalOps</span>
          <button
            onClick={onClose}
            className="p-2 rounded-lg text-zinc-400 hover:bg-zinc-800 hover:text-white transition-colors"
            aria-label="Close sidebar"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <Sidebar onNavigate={onClose} />
      </aside>
    </>
  );
}

export default function AppShell({ children }: { children: ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <>
      <MobileSidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <Sidebar />

      <div className="flex-1 flex flex-col min-h-screen overflow-hidden">
        <header className="flex items-center justify-between px-4 py-3 border-b border-zinc-200 dark:border-zinc-800 bg-white/80 dark:bg-zinc-950/80 backdrop-blur-sm">
          <div className="flex items-center gap-3">
            <button
              className="lg:hidden p-2 rounded-lg text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
              aria-label="Toggle sidebar"
              onClick={() => setSidebarOpen(!sidebarOpen)}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
            <span className="text-xl font-bold text-zinc-900 dark:text-white lg:hidden">EvalOps</span>
            <h1 className="text-sm font-medium text-zinc-500 dark:text-zinc-400">Pipeline Intelligence</h1>
          </div>
          <div className="flex items-center gap-3">
            <ConnectionIndicator />
          </div>
        </header>
        <main className="flex-1 overflow-y-auto bg-zinc-50 dark:bg-zinc-950">
          <div className="p-6">{children}</div>
        </main>
      </div>
    </>
  );
}
