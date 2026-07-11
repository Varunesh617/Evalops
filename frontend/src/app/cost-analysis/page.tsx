"use client";

import { useEffect, useState } from "react";
import { costs } from "@/lib/api";
import type { CostReport, CostBucket } from "@/lib/api";

function BucketTable({ title, buckets }: { title: string; buckets: CostBucket[] }) {
  return (
    <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-hidden">
      <div className="px-6 py-3 border-b border-zinc-200 dark:border-zinc-800">
        <h3 className="text-sm font-semibold text-zinc-900 dark:text-white">{title}</h3>
      </div>
      <table className="w-full">
        <thead>
          <tr className="border-b border-zinc-200 dark:border-zinc-800">
            <th className="text-left text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-2">
              Label
            </th>
            <th className="text-right text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-2">
              Cost
            </th>
            <th className="text-right text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-2">
              Entries
            </th>
            <th className="text-right text-xs font-semibold text-zinc-500 uppercase tracking-wider px-6 py-2">
              Tokens
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {buckets.length === 0 ? (
            <tr>
              <td colSpan={4} className="px-6 py-6 text-center text-sm text-zinc-500">
                No data.
              </td>
            </tr>
          ) : (
            buckets.map((b, i) => (
              <tr key={i} className="hover:bg-zinc-50 dark:hover:bg-zinc-800/50">
                <td className="px-6 py-3 text-sm text-zinc-800 dark:text-zinc-200 font-mono truncate max-w-[200px]">
                  {b.label}
                </td>
                <td className="px-6 py-3 text-sm text-right text-zinc-600 dark:text-zinc-400">
                  ${b.total_cost_usd.toFixed(4)}
                </td>
                <td className="px-6 py-3 text-sm text-right text-zinc-600 dark:text-zinc-400">
                  {b.entry_count}
                </td>
                <td className="px-6 py-3 text-sm text-right text-zinc-600 dark:text-zinc-400">
                  {b.total_tokens.toLocaleString()}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

export default function CostAnalysisPage() {
  const [report, setReport] = useState<CostReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(30);

  useEffect(() => {
    let active = true;
    setLoading(true);
    costs
      .report({ days, forecastDays: 7 })
      .then((data) => {
        if (active) setReport(data);
      })
      .catch(() => {
        if (active) setReport(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [days]);

  const forecastTotal =
    report?.forecasts.reduce((sum, f) => sum + f.projected_cost_usd, 0) ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-zinc-900 dark:text-white">Cost Analysis</h2>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
            Spend breakdown, forecasts, and anomaly detection
          </p>
        </div>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-900 text-zinc-900 dark:text-white"
        >
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
      </div>

      {loading ? (
        <p className="text-sm text-zinc-500">Loading cost report...</p>
      ) : !report ? (
        <p className="text-sm text-zinc-500">Cost data unavailable.</p>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5">
              <p className="text-xs font-semibold text-zinc-500 uppercase tracking-wide">
                Total Cost
              </p>
              <p className="text-2xl font-bold text-zinc-900 dark:text-white mt-1">
                ${report.total_cost_usd.toFixed(2)}
              </p>
            </div>
            <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5">
              <p className="text-xs font-semibold text-zinc-500 uppercase tracking-wide">
                Entries
              </p>
              <p className="text-2xl font-bold text-zinc-900 dark:text-white mt-1">
                {report.total_entries.toLocaleString()}
              </p>
            </div>
            <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5">
              <p className="text-xs font-semibold text-zinc-500 uppercase tracking-wide">
                Forecast (7d)
              </p>
              <p className="text-2xl font-bold text-zinc-900 dark:text-white mt-1">
                ${forecastTotal.toFixed(2)}
              </p>
            </div>
          </div>

          {report.anomalies.length > 0 && (
            <div className="bg-red-50 dark:bg-red-900/20 rounded-lg border border-red-200 dark:border-red-800 p-6">
              <h3 className="text-sm font-semibold text-red-800 dark:text-red-300 mb-3">
                Cost Anomalies ({report.anomalies.length})
              </h3>
              <div className="space-y-2">
                {report.anomalies.map((a, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between text-sm"
                  >
                    <span className="text-zinc-700 dark:text-zinc-300 font-mono truncate">
                      {a.pipeline_id || a.model || a.entry_id}
                    </span>
                    <span className="text-red-700 dark:text-red-400 ml-4 shrink-0">
                      ${a.cost_usd.toFixed(4)} vs ${a.expected_cost_usd.toFixed(4)} (
                      {a.deviation_ratio.toFixed(1)}x)
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <BucketTable title="By Pipeline" buckets={report.by_pipeline} />
            <BucketTable title="By Model" buckets={report.by_model} />
          </div>
          <BucketTable title="By User" buckets={report.by_user} />
        </>
      )}
    </div>
  );
}
