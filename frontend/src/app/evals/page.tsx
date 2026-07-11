"use client";

import { useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { evals, type EvalResult, type EvalCompareResponse } from "@/lib/api";

const AVAILABLE_METRICS = [
  "faithfulness",
  "context_relevance",
  "trajectory_coherence",
  "tool_call_accuracy",
  "cost_efficiency",
];

function scoresToChartData(
  evalA: EvalResult,
  evalB: EvalResult
): { metric: string; scoreA: number; scoreB: number }[] {
  const allKeys = new Set([
    ...Object.keys(evalA.scores),
    ...Object.keys(evalB.scores),
  ]);
  return Array.from(allKeys).map((key) => ({
    metric: key,
    scoreA: evalA.scores[key] ?? 0,
    scoreB: evalB.scores[key] ?? 0,
  }));
}

export default function EvalsPage() {
  const [view, setView] = useState<"overview" | "compare" | "trend">("overview");

  // --- Overview state ---
  const [lastResult, setLastResult] = useState<EvalResult | null>(null);
  const [trajectoryId, setTrajectoryId] = useState("");
  const [selectedMetrics, setSelectedMetrics] = useState<string[]>(["faithfulness"]);
  const [runLoading, setRunLoading] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  // --- Compare state ---
  const [compareIdA, setCompareIdA] = useState("");
  const [compareIdB, setCompareIdB] = useState("");
  const [compareResult, setCompareResult] = useState<EvalCompareResponse | null>(null);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);

  async function handleRunEval() {
    if (!trajectoryId.trim() || selectedMetrics.length === 0) return;
    setRunLoading(true);
    setRunError(null);
    try {
      const result = await evals.run([
        { trajectory_id: trajectoryId.trim(), metrics: selectedMetrics },
      ]);
      setLastResult(result);
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "Failed to run evaluation");
    } finally {
      setRunLoading(false);
    }
  }

  function toggleMetric(metric: string) {
    setSelectedMetrics((prev) =>
      prev.includes(metric) ? prev.filter((m) => m !== metric) : [...prev, metric]
    );
  }

  async function handleCompare() {
    if (!compareIdA.trim() || !compareIdB.trim()) return;
    setCompareLoading(true);
    setCompareError(null);
    setCompareResult(null);
    try {
      const result = await evals.compare(compareIdA.trim(), compareIdB.trim());
      setCompareResult(result);
    } catch (err) {
      setCompareError(err instanceof Error ? err.message : "Failed to compare evaluations");
    } finally {
      setCompareLoading(false);
    }
  }

  const compareChartData = compareResult
    ? scoresToChartData(compareResult.eval_a, compareResult.eval_b)
    : [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-zinc-900 dark:text-white">
            Evaluations
          </h2>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
            View evaluation scores, compare results, and track trends
          </p>
        </div>
        <div className="flex items-center gap-1 bg-zinc-100 dark:bg-zinc-800 rounded-lg p-1">
          {(["overview", "compare", "trend"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-3 py-1.5 text-xs font-medium rounded transition-colors ${
                view === v
                  ? "bg-white dark:bg-zinc-700 text-zinc-900 dark:text-white shadow-sm"
                  : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
              }`}
            >
              {v.charAt(0).toUpperCase() + v.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {view === "overview" && (
        <>
          {/* Run Evaluation Form */}
          <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5">
            <h3 className="text-sm font-semibold text-zinc-900 dark:text-white mb-3">
              Run Evaluation
            </h3>
            <div className="flex flex-wrap items-end gap-4">
              <div className="flex-1 min-w-[200px]">
                <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                  Trajectory ID
                </label>
                <input
                  type="text"
                  value={trajectoryId}
                  onChange={(e) => setTrajectoryId(e.target.value)}
                  placeholder="e.g. traj_abc123"
                  className="w-full px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-800 text-zinc-900 dark:text-white placeholder:text-zinc-400"
                />
              </div>
              <div className="flex-1 min-w-[200px]">
                <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                  Metrics
                </label>
                <div className="flex flex-wrap gap-2">
                  {AVAILABLE_METRICS.map((metric) => (
                    <label
                      key={metric}
                      className="inline-flex items-center gap-1.5 text-xs text-zinc-600 dark:text-zinc-400 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={selectedMetrics.includes(metric)}
                        onChange={() => toggleMetric(metric)}
                        className="rounded border-zinc-300 dark:border-zinc-600"
                      />
                      {metric}
                    </label>
                  ))}
                </div>
              </div>
              <button
                onClick={handleRunEval}
                disabled={runLoading || !trajectoryId.trim() || selectedMetrics.length === 0}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {runLoading ? "Running..." : "Run Eval"}
              </button>
            </div>
            {runError && (
              <p className="mt-3 text-xs text-red-600 dark:text-red-400">{runError}</p>
            )}
          </div>

          {/* Score Cards */}
          {lastResult ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5">
                <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
                  Aggregate Score
                </p>
                <div className="mt-2 flex items-end gap-2">
                  <span className="text-3xl font-bold text-zinc-900 dark:text-white">
                    {lastResult.aggregate_score.toFixed(2)}
                  </span>
                </div>
                <div className="mt-3 h-2 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 rounded-full transition-all"
                    style={{ width: `${lastResult.aggregate_score * 100}%` }}
                  />
                </div>
              </div>
              {Object.entries(lastResult.scores).map(([metric, score]) => (
                <div
                  key={metric}
                  className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5"
                >
                  <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
                    {metric}
                  </p>
                  <div className="mt-2 flex items-end gap-2">
                    <span className="text-3xl font-bold text-zinc-900 dark:text-white">
                      {score.toFixed(2)}
                    </span>
                    <span className="text-xs text-zinc-400 mb-1">avg score</span>
                  </div>
                  <div className="mt-3 h-2 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-500 rounded-full transition-all"
                      style={{ width: `${score * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-8 text-center">
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                No evaluations yet. Run a pipeline evaluation to see results.
              </p>
            </div>
          )}
        </>
      )}

      {view === "compare" && (
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Side-by-Side Comparison
          </h3>
          <div className="flex flex-wrap items-end gap-4 mb-6">
            <div className="flex-1 min-w-[180px]">
              <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                Eval A ID
              </label>
              <input
                type="text"
                value={compareIdA}
                onChange={(e) => setCompareIdA(e.target.value)}
                placeholder="e.g. eval_abc123"
                className="w-full px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-800 text-zinc-900 dark:text-white placeholder:text-zinc-400"
              />
            </div>
            <div className="flex-1 min-w-[180px]">
              <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                Eval B ID
              </label>
              <input
                type="text"
                value={compareIdB}
                onChange={(e) => setCompareIdB(e.target.value)}
                placeholder="e.g. eval_def456"
                className="w-full px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-800 text-zinc-900 dark:text-white placeholder:text-zinc-400"
              />
            </div>
            <button
              onClick={handleCompare}
              disabled={compareLoading || !compareIdA.trim() || !compareIdB.trim()}
              className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {compareLoading ? "Comparing..." : "Compare"}
            </button>
          </div>
          {compareError && (
            <p className="mb-4 text-xs text-red-600 dark:text-red-400">{compareError}</p>
          )}
          {compareResult ? (
            <>
              {compareResult.winner && (
                <p className="text-sm text-zinc-500 dark:text-zinc-400 mb-3">
                  Winner: <span className="font-medium text-zinc-900 dark:text-white">{compareResult.winner}</span>
                </p>
              )}
              <div className="h-80">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={compareChartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
                    <XAxis dataKey="metric" tick={{ fontSize: 11, fill: "#71717a" }} />
                    <YAxis domain={[0, 1]} tick={{ fontSize: 12, fill: "#71717a" }} />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "#18181b",
                        border: "1px solid #27272a",
                        borderRadius: "6px",
                        color: "#fafafa",
                        fontSize: "12px",
                      }}
                    />
                    <Legend />
                    <Bar dataKey="scoreA" name="Eval A" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="scoreB" name="Eval B" fill="#a855f7" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </>
          ) : (
            <div className="h-80 flex items-center justify-center text-sm text-zinc-400">
              Enter two evaluation IDs and click Compare to see results
            </div>
          )}
        </div>
      )}

      {view === "trend" && (
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Score Trends Over Time
          </h3>
          <div className="h-80 flex items-center justify-center text-sm text-zinc-400">
            Trend data will appear as you run more evaluations
          </div>
        </div>
      )}
    </div>
  );
}
