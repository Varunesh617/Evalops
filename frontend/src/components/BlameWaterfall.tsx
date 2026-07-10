"use client";

import type { BlameAttribution } from "@/lib/api";

interface BlameWaterfallProps {
  blame: BlameAttribution;
}

export default function BlameWaterfall({ blame }: BlameWaterfallProps) {
  const confidencePct = Math.round(blame.confidence * 100);

  return (
    <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
      <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
        Root Cause Analysis
      </h3>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <div>
          <p className="text-xs text-zinc-500 mb-1">Failure Step</p>
          <p className="text-lg font-medium text-zinc-900 dark:text-white">
            Step {blame.failure_step + 1}
          </p>
        </div>
        <div>
          <p className="text-xs text-zinc-500 mb-1">Failure Type</p>
          <p className="text-lg font-medium text-zinc-900 dark:text-white">
            {blame.failure_type}
          </p>
        </div>
        <div>
          <p className="text-xs text-zinc-500 mb-1">Confidence</p>
          <div className="flex items-center gap-2">
            <div className="flex-1 h-2 bg-zinc-200 dark:bg-zinc-700 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${
                  confidencePct >= 80
                    ? "bg-emerald-500"
                    : confidencePct >= 50
                      ? "bg-amber-500"
                      : "bg-red-500"
                }`}
                style={{ width: `${confidencePct}%` }}
              />
            </div>
            <span className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
              {confidencePct}%
            </span>
          </div>
        </div>
      </div>

      <div className="mb-6">
        <p className="text-xs text-zinc-500 mb-2">Root Cause</p>
        <p className="text-sm text-zinc-800 dark:text-zinc-200 bg-zinc-50 dark:bg-zinc-800/50 rounded p-3">
          {blame.root_cause}
        </p>
      </div>

      {blame.contributing_factors.length > 0 && (
        <div className="mb-6">
          <p className="text-xs text-zinc-500 mb-2">Contributing Factors</p>
          <ul className="space-y-1">
            {blame.contributing_factors.map((factor, i) => (
              <li
                key={i}
                className="text-sm text-zinc-700 dark:text-zinc-300 flex items-start gap-2"
              >
                <span className="text-amber-500 mt-0.5">▸</span>
                {factor}
              </li>
            ))}
          </ul>
        </div>
      )}

      {blame.suggested_fixes.length > 0 && (
        <div>
          <p className="text-xs text-zinc-500 mb-2">Suggested Fixes</p>
          <ul className="space-y-1">
            {blame.suggested_fixes.map((fix, i) => (
              <li
                key={i}
                className="text-sm text-zinc-700 dark:text-zinc-300 flex items-start gap-2"
              >
                <span className="text-emerald-500 mt-0.5">→</span>
                {fix}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
