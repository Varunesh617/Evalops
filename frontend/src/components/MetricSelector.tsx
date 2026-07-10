"use client";

import type { MetricPreference } from "@/lib/api";

interface MetricSelectorProps {
  metrics: MetricPreference[];
  onChange: (metrics: MetricPreference[]) => void;
}

export default function MetricSelector({ metrics, onChange }: MetricSelectorProps) {
  const toggleMetric = (index: number) => {
    const updated = metrics.map((m, i) =>
      i === index ? { ...m, enabled: !m.enabled } : m
    );
    onChange(updated);
  };

  const updateWeight = (index: number, weight: number) => {
    const updated = metrics.map((m, i) =>
      i === index ? { ...m, weight } : m
    );
    onChange(updated);
  };

  return (
    <div className="space-y-2">
      {metrics.map((metric, i) => (
        <div
          key={metric.name}
          className={`flex items-center gap-3 p-3 rounded-lg border transition-colors ${
            metric.enabled
              ? "border-blue-200 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-900/10"
              : "border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900"
          }`}
        >
          <button
            onClick={() => toggleMetric(i)}
            className={`w-5 h-5 rounded border-2 flex items-center justify-center transition-colors ${
              metric.enabled
                ? "bg-blue-600 border-blue-600 text-white"
                : "border-zinc-300 dark:border-zinc-600"
            }`}
          >
            {metric.enabled && (
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            )}
          </button>
          <div className="flex-1 min-w-0">
            <span className="text-sm font-medium text-zinc-900 dark:text-white">
              {metric.name}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-zinc-500">Weight</label>
            <input
              type="number"
              min={0}
              max={10}
              step={0.1}
              value={metric.weight}
              onChange={(e) => updateWeight(i, parseFloat(e.target.value) || 0)}
              disabled={!metric.enabled}
              className="w-16 px-2 py-1 text-xs text-right border border-zinc-200 dark:border-zinc-700 rounded bg-white dark:bg-zinc-800 text-zinc-900 dark:text-white disabled:opacity-40"
            />
          </div>
        </div>
      ))}
    </div>
  );
}
