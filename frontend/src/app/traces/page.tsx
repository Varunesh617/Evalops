"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { traces } from "@/lib/api";
import type { Trace } from "@/lib/api";

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  running: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  completed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

const PAGE_SIZE = 50;

export default function TracesPage() {
  const [traceList, setTraceList] = useState<Trace[]>([]);
  const [loading, setLoading] = useState(true);
  const [pipelineFilter, setPipelineFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [minCost, setMinCost] = useState("");
  const [maxCost, setMaxCost] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    setPage(1);
  }, [pipelineFilter, statusFilter, minCost, maxCost]);

  useEffect(() => {
    async function load() {
      try {
        const data = await traces.list(
          page,
          PAGE_SIZE,
          pipelineFilter || undefined,
          statusFilter || undefined,
          minCost ? Number(minCost) : undefined,
          maxCost ? Number(maxCost) : undefined,
        );
        setTraceList(data.traces ?? []);
        setTotal(data.total);
      } catch {
        // API not available
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [page, pipelineFilter, statusFilter, minCost, maxCost]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-zinc-900 dark:text-white">
          Traces
        </h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
          Inspect pipeline execution traces and step-by-step details
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <input
          type="text"
          placeholder="Pipeline ID..."
          value={pipelineFilter}
          onChange={(e) => setPipelineFilter(e.target.value)}
          className="px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-900 text-zinc-900 dark:text-white placeholder:text-zinc-400"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-900 text-zinc-900 dark:text-white"
        >
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
        </select>
        <input
          type="number"
          placeholder="Min cost"
          value={minCost}
          onChange={(e) => setMinCost(e.target.value)}
          step="0.01"
          min="0"
          className="w-24 px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-900 text-zinc-900 dark:text-white placeholder:text-zinc-400"
        />
        <input
          type="number"
          placeholder="Max cost"
          value={maxCost}
          onChange={(e) => setMaxCost(e.target.value)}
          step="0.01"
          min="0"
          className="w-24 px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-900 text-zinc-900 dark:text-white placeholder:text-zinc-400"
        />
      </div>

      <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-zinc-200 dark:border-zinc-800">
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Trace ID
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Pipeline
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Status
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Cost
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Steps
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Started
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {loading ? (
              <tr>
                <td colSpan={6} className="px-6 py-8 text-center text-sm text-zinc-500">
                  Loading traces...
                </td>
              </tr>
            ) : traceList.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-6 py-8 text-center text-sm text-zinc-500">
                  No traces found.
                </td>
              </tr>
            ) : (
              traceList.map((t) => (
                <tr key={t.id} className="hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors">
                  <td className="px-6 py-4">
                    <Link
                      href={`/traces/${t.id}`}
                      className="text-sm font-medium text-blue-600 dark:text-blue-400 font-mono hover:underline"
                    >
                      {t.id}
                    </Link>
                  </td>
                  <td className="px-6 py-4 text-sm text-zinc-600 dark:text-zinc-400 font-mono">
                    {t.pipeline_id}
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${STATUS_STYLES[t.status] ?? STATUS_STYLES.pending}`}
                    >
                      {t.status}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-sm text-zinc-600 dark:text-zinc-400">
                    ${t.total_cost_usd.toFixed(4)}
                  </td>
                  <td className="px-6 py-4 text-sm text-zinc-600 dark:text-zinc-400">
                    {t.steps.length}
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

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="px-4 py-2 text-sm font-medium rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Previous
          </button>
          <span className="text-sm text-zinc-500 dark:text-zinc-400">
            Page {page} of {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            className="px-4 py-2 text-sm font-medium rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
