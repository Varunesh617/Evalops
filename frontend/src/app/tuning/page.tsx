"use client";

import { useEffect, useState } from "react";
import {
  tuning,
  type MetricPreference,
  type FilterPreference,
  type TuningPreset,
  type UserPreferences,
} from "@/lib/api";
import MetricSelector from "@/components/MetricSelector";
import FilterConfig from "@/components/FilterConfig";
import PresetManager from "@/components/PresetManager";

interface SmartDefault {
  setting: string;
  suggested: string | number;
  reason: string;
}

export default function TuningPage() {
  const [metrics, setMetrics] = useState<MetricPreference[]>([]);
  const [filters, setFilters] = useState<FilterPreference[]>([]);
  const [presets, setPresets] = useState<TuningPreset[]>([]);
  const [smartDefaults, setSmartDefaults] = useState<SmartDefault[]>([]);
  const [activePresetId, setActivePresetId] = useState<string | null>(null);
  const [preferences, setPreferences] = useState<UserPreferences | null>(null);
  const [activeTab, setActiveTab] = useState<"metrics" | "filters" | "presets" | "defaults">("metrics");

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  const clearSaveMessage = () => setTimeout(() => setSaveMessage(null), 3000);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        setLoading(true);
        setError(null);

        const [prefs, presetList, defaults] = await Promise.all([
          tuning.getPreferences(),
          tuning.listPresets(),
          tuning.getSmartDefaults(),
        ]);

        if (cancelled) return;

        setPreferences(prefs);
        setMetrics(prefs.metrics);
        setFilters(prefs.filters);
        setPresets(presetList);

        const recs = (defaults as Record<string, unknown>).recommendations as
          | SmartDefault[]
          | undefined;
        setSmartDefaults(recs ?? []);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load tuning data");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => { cancelled = true; };
  }, []);

  const handleSaveMetrics = async () => {
    try {
      setSaving(true);
      setError(null);
      const res = await tuning.configureMetrics("default", metrics);
      setPreferences(res.preferences);
      setSaveMessage("Metric config saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save metrics");
    } finally {
      setSaving(false);
    }
  };

  const handleSaveFilters = async () => {
    try {
      setSaving(true);
      setError(null);
      const res = await tuning.configureFilters("default", filters);
      setPreferences(res.preferences);
      setSaveMessage("Filter config saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save filters");
    } finally {
      setSaving(false);
    }
  };

  const handleApplyPreset = async (presetId: string) => {
    try {
      setSaving(true);
      setError(null);
      const res = await tuning.applyPreset(presetId);
      setActivePresetId(presetId);
      setPreferences(res.preferences);
      setMetrics(res.preferences.metrics);
      setFilters(res.preferences.filters);
      setSaveMessage(`Preset "${presets.find((p) => p.preset_id === presetId)?.name ?? presetId}" applied`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to apply preset");
    } finally {
      setSaving(false);
    }
  };

  const handleApplySmartDefault = async (rec: SmartDefault) => {
    if (!preferences) return;
    try {
      setSaving(true);
      setError(null);
      const updated: UserPreferences = {
        ...preferences,
        optimization: {
          ...preferences.optimization,
          [rec.setting]: rec.suggested,
        },
      };
      const res = await tuning.updatePreferences(updated);
      setPreferences(res);
      setSaveMessage(`${rec.setting} updated to ${String(rec.suggested)}`);
      setTimeout(clearSaveMessage, 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to apply recommendation");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-bold text-zinc-900 dark:text-white">Tuning</h2>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
            Configure metrics, filters, and optimization preferences
          </p>
        </div>
        <div className="flex items-center justify-center py-12">
          <div className="text-sm text-zinc-500 dark:text-zinc-400">Loading tuning configuration…</div>
        </div>
      </div>
    );
  }

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

      {error && (
        <div className="px-4 py-3 text-sm rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400">
          {error}
        </div>
      )}

      {saveMessage && (
        <div className="px-4 py-3 text-sm rounded-lg bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400">
          {saveMessage}
        </div>
      )}

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
            <button
              onClick={handleSaveMetrics}
              disabled={saving}
              className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save Metric Config"}
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
            <button
              onClick={handleSaveFilters}
              disabled={saving}
              className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save Filter Config"}
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
            presets={presets}
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
            {smartDefaults.map((rec) => (
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
                  <button
                    onClick={() => handleApplySmartDefault(rec)}
                    disabled={saving || !preferences}
                    className="px-2 py-1 text-xs font-medium rounded bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-200 dark:hover:bg-emerald-900/50 transition-colors disabled:opacity-50"
                  >
                    {saving ? "…" : "Apply"}
                  </button>
                </div>
              </div>
            ))}
            {smartDefaults.length === 0 && (
              <p className="text-sm text-zinc-500 dark:text-zinc-400 text-center py-4">
                No recommendations available for your current usage patterns.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
