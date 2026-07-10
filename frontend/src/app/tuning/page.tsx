"use client";

import { useEffect, useState } from "react";
import type { MetricPreference, FilterPreference, UserPreferences, TuningPreset } from "@/lib/api";
import MetricSelector from "@/components/MetricSelector";
import FilterConfig from "@/components/FilterConfig";
import PresetManager from "@/components/PresetManager";

const DEFAULT_METRICS: MetricPreference[] = [
  { name: "faithfulness", enabled: true, weight: 1.0 },
  { name: "context_relevance", enabled: true, weight: 1.0 },
  { name: "trajectory_coherence", enabled: true, weight: 1.0 },
  { name: "tool_call_accuracy", enabled: true, weight: 1.0 },
  { name: "guardrail_fp_rate", enabled: false, weight: 0.5 },
  { name: "cost_efficiency", enabled: true, weight: 1.0 },
];

const DEFAULT_FILTERS: FilterPreference[] = [
  { name: "prompt_injection", enabled: true, threshold: 0.5, priority: 100 },
  { name: "pii", enabled: true, threshold: 0.6, priority: 90 },
  { name: "toxicity", enabled: true, threshold: 0.5, priority: 80 },
  { name: "faithfulness_check", enabled: false, threshold: 0.5, priority: 50 },
  { name: "citation_validator", enabled: false, threshold: 0.5, priority: 40 },
];

const MOCK_PRESETS: TuningPreset[] = [
  {
    preset_id: "builtin-healthcare",
    name: "Healthcare",
    description: "High-faithfulness configuration for medical Q&A with strict guardrails.",
    domain: "healthcare",
    preferences: {} as UserPreferences,
    is_builtin: true,
  },
  {
    preset_id: "builtin-finance",
    name: "Finance",
    description: "Balanced cost/quality for financial document analysis.",
    domain: "finance",
    preferences: {} as UserPreferences,
    is_builtin: true,
  },
  {
    preset_id: "builtin-general",
    name: "General Purpose",
    description: "Default settings suitable for most use cases.",
    domain: "general",
    preferences: {} as UserPreferences,
    is_builtin: true,
  },
];

const SMART_DEFAULTS = {
  recommendations: [
    { setting: "retrieval_top_k", suggested: 10, reason: "Based on avg query complexity" },
    { setting: "reranker_model", suggested: "cross-encoder", reason: "Best quality/cost ratio" },
    { setting: "temperature", suggested: 0.3, reason: "Low hallucination target" },
    { setting: "guardrails", suggested: "enabled", reason: "High-stakes domain detected" },
  ],
};

export default function TuningPage() {
  const [metrics, setMetrics] = useState<MetricPreference[]>(DEFAULT_METRICS);
  const [filters, setFilters] = useState<FilterPreference[]>(DEFAULT_FILTERS);
  const [activePresetId, setActivePresetId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"metrics" | "filters" | "presets" | "defaults">("metrics");

  const handleApplyPreset = (presetId: string) => {
    setActivePresetId(presetId);
    // In production: fetch preset, update metrics/filters
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-zinc-900 dark:text-white">
          Tuning
        </h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
          Configure metrics, filters, and optimization preferences
        </p>
      </div>

      <div className="flex items-center gap-1 bg-zinc-100 dark:bg-zinc-800 rounded-lg p-1 w-fit">
        {(["metrics", "filters", "presets", "defaults"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-1.5 text-xs font-medium rounded transition-colors ${
              activeTab === tab
                ? "bg-white dark:bg-zinc-700 text-zinc-900 dark:text-white shadow-sm"
                : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
            }`}
          >
            {tab === "defaults" ? "Smart Defaults" : tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {activeTab === "metrics" && (
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Metric Selection & Weights
          </h3>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mb-4">
            Toggle metrics on/off and adjust their weights for composite scoring.
          </p>
          <MetricSelector metrics={metrics} onChange={setMetrics} />
          <div className="mt-4 flex justify-end">
            <button className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors">
              Save Metric Config
            </button>
          </div>
        </div>
      )}

      {activeTab === "filters" && (
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Filter Configuration
          </h3>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mb-4">
            Set detection thresholds and priority ordering for content filters.
          </p>
          <FilterConfig filters={filters} onChange={setFilters} />
          <div className="mt-4 flex justify-end">
            <button className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors">
              Save Filter Config
            </button>
          </div>
        </div>
      )}

      {activeTab === "presets" && (
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Preset Manager
          </h3>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mb-4">
            Apply pre-configured tuning profiles or create your own.
          </p>
          <PresetManager
            presets={MOCK_PRESETS}
            activePresetId={activePresetId}
            onApply={handleApplyPreset}
          />
        </div>
      )}

      {activeTab === "defaults" && (
        <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
            Smart Defaults
          </h3>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mb-4">
            AI-powered configuration recommendations based on your usage patterns.
          </p>
          <div className="space-y-3">
            {SMART_DEFAULTS.recommendations.map((rec) => (
              <div
                key={rec.setting}
                className="flex items-center justify-between p-4 rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-800/50"
              >
                <div>
                  <p className="text-sm font-medium text-zinc-900 dark:text-white font-mono">
                    {rec.setting}
                  </p>
                  <p className="text-xs text-zinc-500 mt-0.5">{rec.reason}</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-emerald-600 dark:text-emerald-400 font-mono">
                    {String(rec.suggested)}
                  </span>
                  <button className="px-2 py-1 text-xs font-medium rounded bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-200 dark:hover:bg-emerald-900/50 transition-colors">
                    Apply
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
