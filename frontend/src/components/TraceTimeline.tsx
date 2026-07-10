"use client";

import type { TraceStep } from "@/lib/api";

interface TraceTimelineProps {
  steps: TraceStep[];
}

const STATUS_COLORS: Record<string, string> = {
  success: "bg-emerald-500",
  completed: "bg-emerald-500",
  running: "bg-blue-500",
  failed: "bg-red-500",
  pending: "bg-zinc-300 dark:bg-zinc-600",
};

export default function TraceTimeline({ steps }: TraceTimelineProps) {
  if (steps.length === 0) {
    return (
      <p className="text-sm text-zinc-500 dark:text-zinc-400 italic">
        No steps recorded.
      </p>
    );
  }

  return (
    <div className="relative">
      <div className="absolute left-4 top-0 bottom-0 w-px bg-zinc-200 dark:bg-zinc-700" />
      <div className="space-y-0">
        {steps.map((step, i) => {
          const dotColor = STATUS_COLORS[step.status] ?? "bg-zinc-400";
          return (
            <div key={step.step_id} className="relative flex items-start gap-4 py-4">
              <div className="relative z-10 flex items-center justify-center w-8 h-8 rounded-full bg-white dark:bg-zinc-900 border-2 border-zinc-200 dark:border-zinc-700">
                <span className={`w-3 h-3 rounded-full ${dotColor}`} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-zinc-900 dark:text-white">
                    Step {step.step_id + 1}: {step.step_type}
                  </span>
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                      step.status === "success" || step.status === "completed"
                        ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                        : step.status === "failed"
                          ? "bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                          : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
                    }`}
                  >
                    {step.status}
                  </span>
                </div>
                {step.input_text && (
                  <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400 line-clamp-2">
                    Input: {step.input_text}
                  </p>
                )}
                {step.output_text && (
                  <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400 line-clamp-2">
                    Output: {step.output_text}
                  </p>
                )}
                <div className="mt-2 flex items-center gap-4 text-xs text-zinc-400">
                  <span>{step.duration_ms.toFixed(0)}ms</span>
                  <span>{step.tokens_used.toLocaleString()} tokens</span>
                  <span>${step.cost_usd.toFixed(4)}</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
