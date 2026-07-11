"use client";

import { useEffect, useState } from "react";
import { useError } from "@/lib/error-context";
import useHealthCheck from "@/hooks/useHealthCheck";
import { CardSkeleton } from "@/components/LoadingSkeleton";
import { pipelines as pipelinesApi, traces as tracesApi } from "@/lib/api";
import type { Trace } from "@/lib/api";

export default function DashboardPage() {
  const { showToast } = useError();
  const { status } = useHealthCheck();

  const [stats, setStats] = useState({
    pipelines: 0,
    traces: 0,
    evals: 0,
    totalCost: 0,
  });
  const [recentTraces, setRecentTraces] = useState<Trace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Update error context when error changes
  useEffect(() => {
    if (error) {
      setError(null); // This would be handled differently in real implementation
    }
  }, [error]);

  // Update error when connection status changes
  useEffect(() => {
    if (!status.connected) {
      showToast("API disconnected", "error", {
        label: "Retry Now",
        onClick: () => {
          window.location.reload();
        }
      });
    }
  }, [status.connected, showToast]);

  useEffect(() => {
    async function load() {
      try {
        const [plRes, trRes] = await Promise.allSettled([
          pipelinesApi.list(1, 1),
          tracesApi.list(1, 100),
        ]);

        let pipelineCount = 0;
        let traceCount = 0;
        let totalCost = 0;
        let recent: Trace[] = [];

        if (plRes.status === "fulfilled") {
          pipelineCount = plRes.value.total ?? 0;
        }

        if (trRes.status === "fulfilled") {
          const trData = trRes.value;
          traceCount = trData.total ?? 0;
          recent = trData.traces?.slice(0, 5) ?? [];
          totalCost = trData.traces?.reduce((sum, t) => sum + (t.total_cost_usd ?? 0), 0) ?? 0;
        }

        if (status.connected) {
          showToast("Dashboard data loaded successfully", "success");
        }

        setStats({ pipelines: pipelineCount, traces: traceCount, evals: 0, totalCost });
        setRecentTraces(recent);

        if (plRes.status === "rejected" || trRes.status === "rejected") {
          setError("Some data failed to load — showing partial results.");
        }
      } catch (err) {
        setError("Unable to reach the API. Showing cached defaults.");
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [status.connected, showToast]);

  const quickActions = [
    { label: "Create Pipeline", href: "/pipelines", icon: "🔗" },
    { label: "Run Evaluation", href: "/evals", icon: "✅" },
    { label: "Start Sweep", href: "/optimization", icon: "⚡" },
  ];

  return (
    <div className="space-y-6">
      {loading && (
        <div className="mb-6">
          <div className="h-8 w-48 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse mb-2" />
          <div className="h-4 w-64 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
        </div>
      )}

      {(error || !status.connected) && (
        <div className="rounded-lg border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 px-4 py-3 text-sm text-amber-800 dark:text-amber-300">
          {error ?? "API is disconnected. Some features may not work."}
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {loading ? (
          <>
            <CardSkeleton />
            <CardSkeleton />
            <CardSkeleton />
            <CardSkeleton />
          </>
        ) : (
          <>
            <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5">
              <div className="flex justify-between items-start">
                <div className="space-y-2">
                  <div className="h-4 w-24 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
                  <div className="h-8 w-16 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
                </div>
                <div className="h-8 w-8 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
              </div>
              <div className="mt-3">
                <div className="h-2 w-full bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
              </div>
            </div>
            <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5">
              <div className="flex justify-between items-start">
                <div className="space-y-2">
                  <div className="h-4 w-24 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
                  <div className="h-8 w-16 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
                </div>
                <div className="h-8 w-8 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
              </div>
              <div className="mt-3">
                <div className="h-2 w-full bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
              </div>
            </div>
            <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5">
              <div className="flex justify-between items-start">
                <div className="space-y-2">
                  <div className="h-4 w-24 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
                  <div className="h-8 w-16 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
                </div>
                <div className="h-8 w-8 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
              </div>
              <div className="mt-3">
                <div className="h-2 w-full bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
              </div>
            </div>
            <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5">
              <div className="flex justify-between items-start">
                <div className="space-y-2">
                  <div className="h-4 w-24 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
                  <div className="h-8 w-16 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
                </div>
                <div className="h-8 w-8 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
              </div>
              <div className="mt-3">
                <div className="h-2 w-full bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
              </div>
            </div>
          </>
        )}
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
            {loading ? (
              <div className="space-y-3">
                <div className="flex items-start gap-3">
                  <div className="w-2 h-2 rounded-full flex-shrink-0 bg-zinc-200 dark:bg-zinc-800 animate-pulse" />
                  <div className="flex-1">
                    <div className="h-4 w-32 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse mb-1" />
                    <div className="h-3 w-24 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <div className="w-2 h-2 rounded-full flex-shrink-0 bg-zinc-200 dark:bg-zinc-800 animate-pulse" />
                  <div className="flex-1">
                    <div className="h-4 w-32 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse mb-1" />
                    <div className="h-3 w-24 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <div className="w-2 h-2 rounded-full flex-shrink-0 bg-zinc-200 dark:bg-zinc-800 animate-pulse" />
                  <div className="flex-1">
                    <div className="h-4 w-32 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse mb-1" />
                    <div className="h-3 w-24 bg-zinc-200 dark:bg-zinc-800 rounded animate-pulse" />
                  </div>
                </div>
              </div>
            ) : recentTraces.length === 0 ? (
              <p className="text-sm text-zinc-400">No recent traces.</p>
            ) : (
              recentTraces.map((trace) => (
                <div
                  key={trace.id}
                  className="flex items-start gap-3 text-sm"
                >
                  <span
                    className={`mt-1 w-2 h-2 rounded-full flex-shrink-0 ${
                      trace.status === "completed"
                        ? "bg-emerald-500"
                        : trace.status === "failed"
                          ? "bg-red-500"
                          : "bg-blue-500"
                    }`}
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-zinc-700 dark:text-zinc-300">
                      Trace <span className="font-mono text-xs">{trace.pipeline_id}</span> — {trace.status}
                    </p>
                    <p className="text-xs text-zinc-400 mt-0.5">
                      {new Date(trace.started_at).toLocaleString()}
                    </p>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}