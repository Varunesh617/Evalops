"use client";

import { useEffect, useState } from "react";
import { diagnosis } from "@/lib/api";
import type {
  CounterfactualResponse,
  RecommendationResponse,
  TrendsResponse,
} from "@/lib/api";
import { useError } from "@/lib/error-context";

const TREND_STYLES: Record<string, string> = {
  increasing: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  decreasing: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  stable: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
};

export default function DiagnosisPage() {
  const { showToast } = useError();
  const [traceId, setTraceId] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [counterfactual, setCounterfactual] = useState<CounterfactualResponse | null>(null);
  const [recommendations, setRecommendations] = useState<RecommendationResponse | null>(null);

  const [trends, setTrends] = useState<TrendsResponse | null>(null);
  const [trendsLoading, setTrendsLoading] = useState(true);
  const [windowDays, setWindowDays] = useState(30);

  useEffect(() => {
    let active = true;
    setTrendsLoading(true);
    diagnosis
      .trends(windowDays, 1)
      .then((data) => {
        if (active) setTrends(data);
      })
      .catch(() => {
        if (active) setTrends(null);
      })
      .finally(() => {
        if (active) setTrendsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [windowDays]);

  async function runDiagnosis() {
    const id = traceId.trim();
    if (!id) return;
    setAnalyzing(true);
    setCounterfactual(null);
    setRecommendations(null);
    try {
      const [cf, rec] = await Promise.allSettled([
        diagnosis.counterfactual({ trace_id: id }),
        diagnosis.recommendations(id),
      ]);
      if (cf.status === "fulfilled") setCounterfactual(cf.value);
      else showToast("Counterfactual analysis failed", "error", { label: "Retry", onClick: runDiagnosis });
      if (rec.status === "fulfilled") setRecommendations(rec.value);
      else showToast("Failed to load recommendations", "error");
    } finally {
      setAnalyzing(false);
    }
  }

  const maxTrendCount = Math.max(1, ...(trends?.data_points ?? []).map((d) => d.count));

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-zinc-900 dark:text-white">Diagnosis</h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
          Counterfactual analysis, recommendations, and failure trends
        </p>
      </div>

      <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
        <h3 className="text-sm font-semibold text-zinc-900 dark:text-white mb-3">
          Analyze a failed trace
        </h3>
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="text"
            placeholder="Trace ID..."
            value={traceId}
            onChange={(e) => setTraceId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runDiagnosis()}
            className="flex-1 min-w-[240px] px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-900 text-zinc-900 dark:text-white placeholder:text-zinc-400 font-mono"
          />
          <button
            onClick={runDiagnosis}
            disabled={analyzing || !traceId.trim()}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {analyzing ? "Analyzing..." : "Diagnose"}
          </button>
        </div>
      </div>

      {counterfactual && (
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-zinc-900 dark:text-white">
              Counterfactual Analysis
            </h3>
            <span className="text-xs text-zinc-500">
              Original score: {counterfactual.original_score.toFixed(3)}
            </span>
          </div>
          {counterfactual.best_intervention && (
            <div className="mb-4 p-3 rounded-lg bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800">
              <p className="text-xs font-semibold text-emerald-700 dark:text-emerald-400 uppercase tracking-wide">
                Best intervention (+{counterfactual.best_delta.toFixed(3)})
              </p>
              <p className="text-sm text-zinc-700 dark:text-zinc-300 mt-1">
                {counterfactual.best_intervention.description}
              </p>
            </div>
          )}
          <div className="space-y-2">
            {counterfactual.results.map((r, i) => (
              <div
                key={i}
                className="flex items-center justify-between p-3 rounded-lg border border-zinc-100 dark:border-zinc-800"
              >
                <div className="min-w-0">
                  <p className="text-sm text-zinc-800 dark:text-zinc-200 truncate">
                    {r.intervention.description}
                  </p>
                  <p className="text-xs text-zinc-500">
                    {r.intervention.change_type} · confidence {(r.confidence * 100).toFixed(0)}%
                  </p>
                </div>
                <span
                  className={`ml-4 shrink-0 text-sm font-medium ${
                    r.improvement_delta > 0
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-zinc-400"
                  }`}
                >
                  {r.improvement_delta > 0 ? "+" : ""}
                  {r.improvement_delta.toFixed(3)}
                </span>
              </div>
            ))}
            {counterfactual.results.length === 0 && (
              <p className="text-sm text-zinc-500">No interventions found.</p>
            )}
          </div>
        </div>
      )}

      {recommendations && (
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-sm font-semibold text-zinc-900 dark:text-white mb-4">
            Recommendations ({recommendations.total})
          </h3>
          <div className="space-y-2">
            {recommendations.recommendations.map((rec, i) => (
              <div
                key={i}
                className="p-3 rounded-lg border border-zinc-100 dark:border-zinc-800"
              >
                <p className="text-sm text-zinc-800 dark:text-zinc-200">
                  {String(rec.action ?? rec.title ?? rec.description ?? `Recommendation ${i + 1}`)}
                </p>
                {rec.rationale != null && (
                  <p className="text-xs text-zinc-500 mt-1">{String(rec.rationale)}</p>
                )}
                <div className="flex flex-wrap items-center gap-2 mt-2">
                  {rec.priority != null && (
                    <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
                      {String(rec.priority)}
                    </span>
                  )}
                  {rec.estimated_cost_delta_usd != null && (
                    <span
                      className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
                        rec.estimated_cost_delta_usd > 0
                          ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                          : "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                      }`}
                      title="Estimated cost change if applied"
                    >
                      {rec.estimated_cost_delta_usd >= 0 ? "+" : ""}
                      ${rec.estimated_cost_delta_usd.toFixed(4)} cost
                    </span>
                  )}
                  {rec.estimated_latency_delta_ms != null && (
                    <span
                      className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
                        rec.estimated_latency_delta_ms > 0
                          ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                          : "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                      }`}
                      title="Estimated latency change if applied"
                    >
                      {rec.estimated_latency_delta_ms >= 0 ? "+" : ""}
                      {rec.estimated_latency_delta_ms.toFixed(0)}ms
                    </span>
                  )}
                </div>
              </div>
            ))}
            {recommendations.recommendations.length === 0 && (
              <p className="text-sm text-zinc-500">No recommendations available.</p>
            )}
          </div>
        </div>
      )}

      <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-zinc-900 dark:text-white">Failure Trends</h3>
          <select
            value={windowDays}
            onChange={(e) => setWindowDays(Number(e.target.value))}
            className="px-3 py-1.5 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-900 text-zinc-900 dark:text-white"
          >
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
        </div>

        {trendsLoading ? (
          <p className="text-sm text-zinc-500">Loading trends...</p>
        ) : !trends || trends.total_failures === 0 ? (
          <p className="text-sm text-zinc-500">No failures recorded in this window.</p>
        ) : (
          <>
            <div className="flex items-center gap-3 mb-4">
              <span
                className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                  TREND_STYLES[trends.trend] ?? TREND_STYLES.stable
                }`}
              >
                {trends.trend}
              </span>
              <span className="text-xs text-zinc-500">
                {trends.total_failures} failures · confidence{" "}
                {(trends.confidence * 100).toFixed(0)}%
              </span>
            </div>
            <div className="flex items-end gap-1 h-32">
              {trends.data_points.map((d, i) => (
                <div
                  key={i}
                  className="flex-1 bg-blue-500/80 rounded-t hover:bg-blue-500 transition-colors"
                  style={{ height: `${(d.count / maxTrendCount) * 100}%` }}
                  title={`${d.period}: ${d.count} failures`}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
