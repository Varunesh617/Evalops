"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { pipelines } from "@/lib/api";
import type { Pipeline, Trace } from "@/lib/api";

const STATUS_STYLES: Record<string, string> = {
  draft: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  active: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  running: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  completed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

export default function PipelineDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [traces, setTraces] = useState<Trace[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [runStatus, setRunStatus] = useState<{ ok: boolean; message: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [pl, tr] = await Promise.allSettled([
        pipelines.get(id),
        pipelines.traces(id),
      ]);
      if (pl.status === "fulfilled") setPipeline(pl.value);
      if (tr.status === "fulfilled") setTraces(tr.value.traces ?? []);
    } catch {
      // API not available
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const handleRun = async () => {
    setRunning(true);
    setRunStatus(null);
    try {
      const result = await pipelines.run(id);
      setRunStatus({ ok: true, message: `Run started (${result.run_id}). Status: ${result.status}` });
      setTimeout(async () => {
        try {
          const tr = await pipelines.traces(id);
          setTraces(tr.traces ?? []);
        } catch {
          // ignore
        }
      }, 2000);
    } catch (err) {
      setRunStatus({ ok: false, message: err instanceof Error ? err.message : "Failed to run pipeline" });
    } finally {
      setRunning(false);
    }
  };

  if (loading) {
    return <p className="text-sm text-zinc-500">Loading pipeline...</p>;
  }

  if (!pipeline) {
    return <p className="text-sm text-zinc-500">Pipeline not found.</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-2xl font-bold text-zinc-900 dark:text-white">
              {pipeline.name}
            </h2>
            <span
              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${STATUS_STYLES[pipeline.status] ?? STATUS_STYLES.draft}`}
            >
              {pipeline.status}
            </span>
          </div>
          {pipeline.description && (
            <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
              {pipeline.description}
            </p>
          )}
          <div className="flex flex-wrap gap-1 mt-2">
            {pipeline.tags.map((tag) => (
              <span
                key={tag}
                className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400"
              >
                {tag}
              </span>
            ))}
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <button
            onClick={handleRun}
            disabled={running}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition-colors disabled:opacity-50"
          >
            {running ? "Running..." : "Run Pipeline"}
          </button>
          {runStatus && (
            <p
              className={`text-xs ${
                runStatus.ok
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-red-600 dark:text-red-400"
              }`}
            >
              {runStatus.message}
            </p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Configuration
          </h3>
          <pre className="text-xs text-zinc-700 dark:text-zinc-300 bg-zinc-50 dark:bg-zinc-800/50 rounded p-4 overflow-x-auto max-h-80 overflow-y-auto">
            {JSON.stringify(pipeline.config, null, 2)}
          </pre>
        </div>

        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Quality Over Time
          </h3>
          <div className="h-64 flex items-center justify-center text-sm text-zinc-400">
            <div className="text-center">
              <p className="text-3xl mb-2">📈</p>
              <p>Chart renders when trace data with quality scores is available.</p>
              <p className="text-xs mt-1">Run the pipeline and evaluate to populate this chart.</p>
            </div>
          </div>
        </div>
      </div>

      <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-hidden">
        <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-800">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white">
            Recent Runs
          </h3>
        </div>
        <table className="w-full">
          <thead>
            <tr className="border-b border-zinc-200 dark:border-zinc-800">
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Trace ID
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Status
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Tokens
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Cost
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Started
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {traces.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-6 py-8 text-center text-sm text-zinc-500">
                  No runs yet.
                </td>
              </tr>
            ) : (
              traces.map((t) => (
                <tr key={t.id} className="hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors">
                  <td className="px-6 py-4">
                    <a
                      href={`/traces/${t.id}`}
                      className="text-sm font-medium text-blue-600 dark:text-blue-400 hover:underline font-mono"
                    >
                      {t.id}
                    </a>
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${STATUS_STYLES[t.status] ?? STATUS_STYLES.draft}`}
                    >
                      {t.status}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-sm text-zinc-600 dark:text-zinc-400">
                    {t.total_tokens.toLocaleString()}
                  </td>
                  <td className="px-6 py-4 text-sm text-zinc-600 dark:text-zinc-400">
                    ${t.total_cost_usd.toFixed(4)}
                  </td>
                  <td className="px-6 py-4 text-xs text-zinc-500">
                    {new Date(t.started_at).toLocaleString()}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
