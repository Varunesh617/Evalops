"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { optimization } from "@/lib/api";
import type { SweepStatusResponse, ParetoPoint, SweepRequest } from "@/lib/api";
import ParetoChart from "@/components/ParetoChart";

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  running: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  completed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

const OBJECTIVE_OPTIONS = ["cost", "quality", "latency", "balanced"] as const;

export default function OptimizationPage() {
  const [sweeps, setSweeps] = useState<SweepStatusResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [pipelineId, setPipelineId] = useState("");
  const [searchSpace, setSearchSpace] = useState("");
  const [objective, setObjective] = useState<string>("balanced");
  const [nTrials, setNTrials] = useState("20");

  const [selectedSweepId, setSelectedSweepId] = useState<string | null>(null);
  const [pareto, setPareto] = useState<ParetoPoint[]>([]);
  const [paretoLoading, setParetoLoading] = useState(false);
  const [paretoError, setParetoError] = useState<string | null>(null);

  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const pollRunningSweeps = useCallback(async (sweepIds: string[]) => {
    if (sweepIds.length === 0) return;
    const updated = await Promise.allSettled(
      sweepIds.map((id) => optimization.status(id))
    );
    setSweeps((prev) => {
      const byId = new Map(prev.map((s) => [s.sweep_id, s]));
      for (const result of updated) {
        if (result.status === "fulfilled") {
          byId.set(result.value.sweep_id, result.value);
        }
      }
      return Array.from(byId.values());
    });
  }, []);

  useEffect(() => {
    pollingRef.current = setInterval(() => {
      setSweeps((current) => {
        const runningIds = current
          .filter((s) => s.status === "running" || s.status === "pending")
          .map((s) => s.sweep_id);
        if (runningIds.length > 0) {
          pollRunningSweeps(runningIds);
        }
        return current;
      });
    }, 5000);

    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, [pollRunningSweeps]);

  useEffect(() => {
    if (!selectedSweepId) {
      setPareto([]);
      setParetoError(null);
      return;
    }

    const selected = sweeps.find((s) => s.sweep_id === selectedSweepId);
    if (selected?.status !== "completed") {
      setPareto([]);
      setParetoError(null);
      return;
    }

    let cancelled = false;
    setParetoLoading(true);
    setParetoError(null);

    optimization
      .pareto(selectedSweepId)
      .then((res) => {
        if (!cancelled) setPareto(res.frontier);
      })
      .catch((err) => {
        if (!cancelled) setParetoError(err instanceof Error ? err.message : "Failed to load Pareto data");
      })
      .finally(() => {
        if (!cancelled) setParetoLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedSweepId, sweeps]);

  const completedSweep = sweeps.find(
    (s) => s.sweep_id === selectedSweepId && s.status === "completed"
  );

  async function handleCreateSweep(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);

    let parsedSpace: Record<string, unknown>;
    try {
      parsedSpace = searchSpace.trim() ? JSON.parse(searchSpace) : {};
    } catch {
      setFormError("Search space must be valid JSON");
      return;
    }

    if (!pipelineId.trim()) {
      setFormError("Pipeline ID is required");
      return;
    }

    setCreating(true);
    try {
      const body: SweepRequest = {
        pipeline_id: pipelineId.trim(),
        search_space: parsedSpace,
        objective: objective,
        n_trials: parseInt(nTrials, 10) || 20,
      };
      const result = await optimization.startSweep(body);
      setSweeps((prev) => [result, ...prev]);
      setShowForm(false);
      setPipelineId("");
      setSearchSpace("");
      setObjective("balanced");
      setNTrials("20");
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to start sweep");
    } finally {
      setCreating(false);
    }
  }

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

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 dark:bg-red-900/20 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-400">
          {error}
        </div>
      )}

      <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-hidden">
        <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-800 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white">
            Active Sweeps
          </h3>
          <button
            onClick={() => setShowForm((prev) => !prev)}
            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors"
          >
            {showForm ? "Cancel" : "+ New Sweep"}
          </button>
        </div>

        {showForm && (
          <form onSubmit={handleCreateSweep} className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-800/30 space-y-3">
            {formError && (
              <p className="text-sm text-red-600 dark:text-red-400">{formError}</p>
            )}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400 mb-1">
                  Pipeline ID
                </label>
                <input
                  type="text"
                  value={pipelineId}
                  onChange={(e) => setPipelineId(e.target.value)}
                  placeholder="pl-abc123"
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm text-zinc-900 dark:text-white placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400 mb-1">
                  Objective
                </label>
                <select
                  value={objective}
                  onChange={(e) => setObjective(e.target.value)}
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm text-zinc-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {OBJECTIVE_OPTIONS.map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400 mb-1">
                  Trials
                </label>
                <input
                  type="number"
                  value={nTrials}
                  onChange={(e) => setNTrials(e.target.value)}
                  min="1"
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm text-zinc-900 dark:text-white placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400 mb-1">
                  Search Space (JSON)
                </label>
                <input
                  type="text"
                  value={searchSpace}
                  onChange={(e) => setSearchSpace(e.target.value)}
                  placeholder='{"top_k": [5,10,15,25]}'
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm text-zinc-900 dark:text-white placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </div>
            <div className="flex justify-end">
              <button
                type="submit"
                disabled={creating}
                className="px-4 py-1.5 text-xs font-medium rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {creating ? "Starting..." : "Start Sweep"}
              </button>
            </div>
          </form>
        )}

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
            ) : sweeps.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-6 py-8 text-center text-sm text-zinc-500">
                  No sweeps yet. Click &ldquo;+ New Sweep&rdquo; to get started.
                </td>
              </tr>
            ) : (
              sweeps.map((s) => (
                <tr
                  key={s.sweep_id}
                  onClick={() =>
                    setSelectedSweepId((prev) => (prev === s.sweep_id ? null : s.sweep_id))
                  }
                  className={`cursor-pointer transition-colors ${
                    selectedSweepId === s.sweep_id
                      ? "bg-blue-50 dark:bg-blue-900/20"
                      : "hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
                  }`}
                >
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

      {paretoLoading ? (
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Pareto Frontier — Cost vs Quality
          </h3>
          <div className="h-80 flex items-center justify-center text-sm text-zinc-500">
            Loading Pareto data...
          </div>
        </div>
      ) : paretoError ? (
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Pareto Frontier — Cost vs Quality
          </h3>
          <div className="h-80 flex items-center justify-center text-sm text-red-500">
            {paretoError}
          </div>
        </div>
      ) : completedSweep ? (
        <ParetoChart frontier={pareto} />
      ) : (
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Pareto Frontier — Cost vs Quality
          </h3>
          <div className="h-80 flex items-center justify-center text-sm text-zinc-500">
            Select a completed sweep to view the Pareto frontier
          </div>
        </div>
      )}

      <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-hidden">
        <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-800">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white">
            Deployment History
          </h3>
        </div>
        <div className="px-6 py-8 text-center text-sm text-zinc-500">
          No deployments yet
        </div>
      </div>
    </div>
  );
}
