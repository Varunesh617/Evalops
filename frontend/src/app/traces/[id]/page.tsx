"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import type { Trace, BlameAttribution } from "@/lib/api";
import TraceTimeline from "@/components/TraceTimeline";
import BlameWaterfall from "@/components/BlameWaterfall";

export default function TraceDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [trace, setTrace] = useState<Trace | null>(null);
  const [blame, setBlame] = useState<BlameAttribution | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const trRes = await fetch(`http://localhost:8000/traces/${id}`);
        if (trRes.ok) {
          const data = await trRes.json();
          setTrace(data);
          if (data.status === "failed") {
            try {
              const blRes = await fetch(`http://localhost:8000/traces/${id}/blame`);
              if (blRes.ok) setBlame(await blRes.json());
            } catch {
              // blame unavailable
            }
          }
        }
      } catch {
        // API not available
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [id]);

  if (loading) {
    return <p className="text-sm text-zinc-500">Loading trace...</p>;
  }

  if (!trace) {
    return <p className="text-sm text-zinc-500">Trace not found.</p>;
  }

  const duration = trace.completed_at
    ? ((new Date(trace.completed_at).getTime() - new Date(trace.started_at).getTime()) / 1000).toFixed(2)
    : "—";

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-zinc-900 dark:text-white">
          Trace Detail
        </h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1 font-mono">
          {trace.id}
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        {[
          { label: "Pipeline", value: trace.pipeline_id },
          { label: "Status", value: trace.status },
          { label: "Tokens", value: trace.total_tokens.toLocaleString() },
          { label: "Cost", value: `$${trace.total_cost_usd.toFixed(4)}` },
          { label: "Duration", value: `${duration}s` },
        ].map((item) => (
          <div
            key={item.label}
            className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-4"
          >
            <p className="text-xs text-zinc-500">{item.label}</p>
            <p className="mt-1 text-sm font-medium text-zinc-900 dark:text-white font-mono">
              {item.value}
            </p>
          </div>
        ))}
      </div>

      <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
        <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-2">
          Query
        </h3>
        <p className="text-sm text-zinc-700 dark:text-zinc-300 bg-zinc-50 dark:bg-zinc-800/50 rounded p-3">
          {trace.query}
        </p>
      </div>

      <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
        <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
          Step-by-Step Timeline
        </h3>
        <TraceTimeline steps={trace.steps} />
      </div>

      {blame && <BlameWaterfall blame={blame} />}

      {!blame && trace.status === "failed" && (
        <div className="bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-800 rounded-lg p-4">
          <p className="text-sm text-amber-700 dark:text-amber-400">
            Blame attribution is available for failed traces. The analysis may still be running.
          </p>
        </div>
      )}
    </div>
  );
}
