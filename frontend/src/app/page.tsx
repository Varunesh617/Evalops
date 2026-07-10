"use client";

import { useEffect, useState } from "react";
import MetricCard from "@/components/MetricCard";

interface DashboardStats {
  pipelines: number;
  traces: number;
  evals: number;
  totalCost: number;
}

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats>({
    pipelines: 0,
    traces: 0,
    evals: 0,
    totalCost: 0,
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [plRes, trRes] = await Promise.allSettled([
          fetch("http://localhost:8000/pipelines?page=1&page_size=1").then((r) => r.json()),
          fetch("http://localhost:8000/traces?page=1&page_size=1").then((r) => r.json()),
        ]);
        setStats({
          pipelines: plRes.status === "fulfilled" ? plRes.value.total ?? 0 : 0,
          traces: trRes.status === "fulfilled" ? trRes.value.total ?? 0 : 0,
          evals: 0,
          totalCost: 0,
        });
      } catch {
        // API not available — show zeros
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const quickActions = [
    { label: "Create Pipeline", href: "/pipelines", icon: "🔗" },
    { label: "Run Evaluation", href: "/evals", icon: "✅" },
    { label: "Start Sweep", href: "/optimization", icon: "⚡" },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-zinc-900 dark:text-white">
          Dashboard
        </h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
          Overview of your EvalOps platform
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label="Total Pipelines"
          value={loading ? "—" : stats.pipelines}
          icon="🔗"
          subtitle="Active configurations"
        />
        <MetricCard
          label="Traces"
          value={loading ? "—" : stats.traces}
          icon="🔍"
          subtitle="Execution records"
        />
        <MetricCard
          label="Evaluations"
          value={loading ? "—" : stats.evals}
          icon="✅"
          subtitle="Scored trajectories"
        />
        <MetricCard
          label="Total Cost"
          value={loading ? "—" : `$${stats.totalCost.toFixed(2)}`}
          icon="💰"
          subtitle="Cumulative spend"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Quick Actions
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {quickActions.map((action) => (
              <a
                key={action.label}
                href={action.href}
                className="flex flex-col items-center gap-2 p-4 rounded-lg border border-zinc-200 dark:border-zinc-700 hover:border-blue-400 dark:hover:border-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/10 transition-colors"
              >
                <span className="text-2xl">{action.icon}</span>
                <span className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
                  {action.label}
                </span>
              </a>
            ))}
          </div>
        </div>

        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Recent Activity
          </h3>
          <div className="space-y-3">
            {[
              { text: "Pipeline \"qa-pipeline\" run completed", time: "2m ago", status: "success" },
              { text: "Eval scores: faithfulness 0.92, context_relevance 0.87", time: "5m ago", status: "info" },
              { text: "Sweep sweep-a1b2c3 found 3 Pareto-optimal configs", time: "12m ago", status: "success" },
              { text: "Plugin \"evalops-phi-filter\" installed", time: "1h ago", status: "info" },
            ].map((item, i) => (
              <div key={i} className="flex items-start gap-3 text-sm">
                <span
                  className={`mt-1 w-2 h-2 rounded-full flex-shrink-0 ${
                    item.status === "success" ? "bg-emerald-500" : "bg-blue-500"
                  }`}
                />
                <div className="flex-1 min-w-0">
                  <p className="text-zinc-700 dark:text-zinc-300">{item.text}</p>
                  <p className="text-xs text-zinc-400 mt-0.5">{item.time}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
