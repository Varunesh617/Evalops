"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: "📊" },
  { href: "/pipelines", label: "Pipelines", icon: "🔗" },
  { href: "/traces", label: "Traces", icon: "🔍" },
  { href: "/evals", label: "Evaluations", icon: "✅" },
  { href: "/optimization", label: "Optimization", icon: "⚡" },
  { href: "/diagnosis", label: "Diagnosis", icon: "🩺" },
  { href: "/cost-analysis", label: "Cost Analysis", icon: "💰" },
  { href: "/tuning", label: "Tuning", icon: "🎛" },
  { href: "/plugins", label: "Plugins", icon: "🧩" },
];

export default function Sidebar({ onNavigate }: { onNavigate?: () => void } = {}) {
  const pathname = usePathname();

  const handleNavigate = () => {
    if (onNavigate) onNavigate();
  };

  return (
    <aside className="hidden lg:flex lg:flex-col lg:w-64 bg-zinc-900 text-zinc-300 border-r border-zinc-800">
      <div className="flex items-center gap-2 px-6 py-5 border-b border-zinc-800">
        <span className="text-xl font-bold text-white tracking-tight">EvalOps</span>
      </div>
      <nav className="flex-1 px-3 py-4 space-y-1">
        {NAV_ITEMS.map((item) => {
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                isActive
                  ? "bg-zinc-800 text-white"
                  : "text-zinc-400 hover:bg-zinc-800/50 hover:text-white"
              }`}
              onClick={handleNavigate}
            >
              <span className="text-base">{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="px-6 py-4 border-t border-zinc-800 text-xs text-zinc-500">
        v0.1.0
      </div>
    </aside>
  );
}
