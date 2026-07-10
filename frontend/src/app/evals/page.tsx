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

const MOCK_SCORES = [
  { metric: "Faithfulness", scoreA: 0.92, scoreB: 0.87 },
  { metric: "Context Relevance", scoreA: 0.85, scoreB: 0.91 },
  { metric: "Trajectory Coherence", scoreA: 0.78, scoreB: 0.82 },
  { metric: "Tool Call Accuracy", scoreA: 0.88, scoreB: 0.84 },
  { metric: "Cost Efficiency", scoreA: 0.71, scoreB: 0.93 },
];

const TREND_DATA = [
  { date: "Jul 1", faithfulness: 0.88, context_relevance: 0.82, coherence: 0.76 },
  { date: "Jul 2", faithfulness: 0.89, context_relevance: 0.84, coherence: 0.77 },
  { date: "Jul 3", faithfulness: 0.91, context_relevance: 0.83, coherence: 0.79 },
  { date: "Jul 4", faithfulness: 0.90, context_relevance: 0.85, coherence: 0.80 },
  { date: "Jul 5", faithfulness: 0.92, context_relevance: 0.87, coherence: 0.82 },
  { date: "Jul 6", faithfulness: 0.93, context_relevance: 0.86, coherence: 0.81 },
  { date: "Jul 7", faithfulness: 0.92, context_relevance: 0.88, coherence: 0.83 },
];

export default function EvalsPage() {
  const [view, setView] = useState<"overview" | "compare" | "trend">("overview");

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
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {MOCK_SCORES.map((s) => (
              <div
                key={s.metric}
                className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5"
              >
                <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
                  {s.metric}
                </p>
                <div className="mt-2 flex items-end gap-2">
                  <span className="text-3xl font-bold text-zinc-900 dark:text-white">
                    {s.scoreA.toFixed(2)}
                  </span>
                  <span className="text-xs text-zinc-400 mb-1">avg score</span>
                </div>
                <div className="mt-3 h-2 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 rounded-full transition-all"
                    style={{ width: `${s.scoreA * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {view === "compare" && (
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Side-by-Side Comparison
          </h3>
          <div className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={MOCK_SCORES} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
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
        </div>
      )}

      {view === "trend" && (
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Score Trends Over Time
          </h3>
          <div className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={TREND_DATA} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#71717a" }} />
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
                <Bar dataKey="faithfulness" name="Faithfulness" fill="#22c55e" radius={[4, 4, 0, 0]} />
                <Bar dataKey="context_relevance" name="Context Relevance" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                <Bar dataKey="coherence" name="Coherence" fill="#f59e0b" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}
