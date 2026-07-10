"use client";

import { useEffect, useState } from "react";
import type { SweepStatusResponse, ParetoPoint } from "@/lib/api";
import ParetoChart from "@/components/ParetoChart";

const MOCK_SWEEPS: SweepStatusResponse[] = [
  {
    sweep_id: "sweep-demo1",
    pipeline_id: "pl-abc123",
    status: "completed",
    trials_completed: 50,
    best_value: 0.91,
    best_params: { retrieval_top_k: 10, reranker_model: "cross-encoder", temperature: 0.3 },
    started_at: new Date(Date.now() - 3600000).toISOString(),
    estimated_completion: null,
  },
  {
    sweep_id: "sweep-demo2",
    pipeline_id: "pl-def456",
    status: "running",
    trials_completed: 23,
    best_value: 0.84,
    best_params: {},
    started_at: new Date(Date.now() - 1800000).toISOString(),
    estimated_completion: new Date(Date.now() + 1800000).toISOString(),
  },
];

const MOCK_PARETO: ParetoPoint[] = [
  { params: { top_k: 5 }, objectives: { cost_usd: 0.002, quality_score: 0.82 }, rank: 1 },
  { params: { top_k: 10 }, objectives: { cost_usd: 0.005, quality_score: 0.89 }, rank: 1 },
  { params: { top_k: 15 }, objectives: { cost_usd: 0.008, quality_score: 0.91 }, rank: 1 },
  { params: { top_k: 25 }, objectives: { cost_usd: 0.015, quality_score: 0.93 }, rank: 1 },
  { params: { top_k: 50 }, objectives: { cost_usd: 0.03, quality_score: 0.94 }, rank: 1 },
];

const MOCK_DOMINATED: ParetoPoint[] = [
  { params: { top_k: 12 }, objectives: { cost_usd: 0.009, quality_score: 0.87 }, rank: 2 },
  { params: { top_k: 30 }, objectives: { cost_usd: 0.025, quality_score: 0.91 }, rank: 2 },
  { params: { top_k: 40 }, objectives: { cost_usd: 0.035, quality_score: 0.92 }, rank: 3 },
];

const DEPLOYMENT_HISTORY = [
  { version: "v1.3.0", config: "top_k=10, temp=0.3", quality: 0.89, cost: "$0.005", date: "Jul 10" },
  { version: "v1.2.1", config: "top_k=5, temp=0.5", quality: 0.82, cost: "$0.003", date: "Jul 7" },
  { version: "v1.2.0", config: "top_k=8, temp=0.4", quality: 0.85, cost: "$0.004", date: "Jul 4" },
  { version: "v1.1.0", config: "top_k=5, temp=0.7", quality: 0.78, cost: "$0.002", date: "Jul 1" },
];

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  running: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  completed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

export default function OptimizationPage() {
  const [sweeps, setSweeps] = useState<SweepStatusResponse[]>(MOCK_SWEEPS);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // In production, fetch real sweep data
    setLoading(false);
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-zinc-900 dark:text-white">
          Optimization
        </h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
          Hyperparameter sweeps, Pareto-optimal configurations, and deployment history
        </p>
      </div>

      <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-hidden">
        <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-800 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white">
            Active Sweeps
          </h3>
          <button className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors">
            + New Sweep
          </button>
        </div>
        <table className="w-full">
          <thead>
            <tr className="border-b border-zinc-200 dark:border-zinc-800">
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Sweep ID
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Pipeline
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Status
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Trials
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Best Score
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {loading ? (
              <tr>
                <td colSpan={5} className="px-6 py-8 text-center text-sm text-zinc-500">
                  Loading sweeps...
                </td>
              </tr>
            ) : (
              sweeps.map((s) => (
                <tr key={s.sweep_id} className="hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors">
                  <td className="px-6 py-4 text-sm font-mono text-blue-600 dark:text-blue-400">
                    {s.sweep_id}
                  </td>
                  <td className="px-6 py-4 text-sm text-zinc-600 dark:text-zinc-400 font-mono">
                    {s.pipeline_id}
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${STATUS_STYLES[s.status] ?? STATUS_STYLES.pending}`}
                    >
                      {s.status}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-sm text-zinc-600 dark:text-zinc-400">
                    {s.trials_completed}
                  </td>
                  <td className="px-6 py-4 text-sm font-medium text-zinc-900 dark:text-white">
                    {s.best_value?.toFixed(4) ?? "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <ParetoChart frontier={MOCK_PARETO} dominated={MOCK_DOMINATED} />

      <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-hidden">
        <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-800">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white">
            Deployment History
          </h3>
        </div>
        <table className="w-full">
          <thead>
            <tr className="border-b border-zinc-200 dark:border-zinc-800">
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Version
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Config
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Quality
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Cost
              </th>
              <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-3">
                Date
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {DEPLOYMENT_HISTORY.map((d) => (
              <tr key={d.version} className="hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors">
                <td className="px-6 py-4 text-sm font-medium text-zinc-900 dark:text-white font-mono">
                  {d.version}
                </td>
                <td className="px-6 py-4 text-sm text-zinc-600 dark:text-zinc-400 font-mono">
                  {d.config}
                </td>
                <td className="px-6 py-4 text-sm text-zinc-600 dark:text-zinc-400">
                  {d.quality.toFixed(2)}
                </td>
                <td className="px-6 py-4 text-sm text-zinc-600 dark:text-zinc-400">
                  {d.cost}
                </td>
                <td className="px-6 py-4 text-xs text-zinc-500">
                  {d.date}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
