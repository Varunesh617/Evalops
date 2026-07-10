"use client";

import type { FilterPreference } from "@/lib/api";

interface FilterConfigProps {
  filters: FilterPreference[];
  onChange: (filters: FilterPreference[]) => void;
}

export default function FilterConfig({ filters, onChange }: FilterConfigProps) {
  const toggleFilter = (index: number) => {
    const updated = filters.map((f, i) =>
      i === index ? { ...f, enabled: !f.enabled } : f
    );
    onChange(updated);
  };

  const updateThreshold = (index: number, threshold: number) => {
    const updated = filters.map((f, i) =>
      i === index ? { ...f, threshold: Math.min(1, Math.max(0, threshold)) } : f
    );
    onChange(updated);
  };

  const updatePriority = (index: number, priority: number) => {
    const updated = filters.map((f, i) =>
      i === index ? { ...f, priority: Math.min(100, Math.max(0, priority)) } : f
    );
    onChange(updated);
  };

  return (
    <div className="space-y-3">
      {filters
        .sort((a, b) => b.priority - a.priority)
        .map((filter, i) => (
          <div
            key={filter.name}
            className={`p-4 rounded-lg border transition-colors ${
              filter.enabled
                ? "border-amber-200 dark:border-amber-800 bg-amber-50/50 dark:bg-amber-900/10"
                : "border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900"
            }`}
          >
            <div className="flex items-center gap-3 mb-3">
              <button
                onClick={() => toggleFilter(i)}
                className={`w-5 h-5 rounded border-2 flex items-center justify-center transition-colors ${
                  filter.enabled
                    ? "bg-amber-500 border-amber-500 text-white"
                    : "border-zinc-300 dark:border-zinc-600"
                }`}
              >
                {filter.enabled && (
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </button>
              <span className="text-sm font-medium text-zinc-900 dark:text-white flex-1">
                {filter.name}
              </span>
              <span className="text-xs text-zinc-500">
                Priority: {filter.priority}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-zinc-500 mb-1">
                  Threshold: {filter.threshold.toFixed(2)}
                </label>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={filter.threshold}
                  onChange={(e) => updateThreshold(i, parseFloat(e.target.value))}
                  disabled={!filter.enabled}
                  className="w-full h-1.5 bg-zinc-200 dark:bg-zinc-700 rounded-full appearance-none cursor-pointer disabled:opacity-40 accent-amber-500"
                />
              </div>
              <div>
                <label className="block text-xs text-zinc-500 mb-1">Priority</label>
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={1}
                  value={filter.priority}
                  onChange={(e) => updatePriority(i, parseInt(e.target.value))}
                  disabled={!filter.enabled}
                  className="w-full h-1.5 bg-zinc-200 dark:bg-zinc-700 rounded-full appearance-none cursor-pointer disabled:opacity-40 accent-amber-500"
                />
              </div>
            </div>
          </div>
        ))}
    </div>
  );
}
