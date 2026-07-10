"use client";

import type { TuningPreset } from "@/lib/api";

interface PresetManagerProps {
  presets: TuningPreset[];
  activePresetId: string | null;
  onApply: (presetId: string) => void;
}

export default function PresetManager({
  presets,
  activePresetId,
  onApply,
}: PresetManagerProps) {
  const builtin = presets.filter((p) => p.is_builtin);
  const custom = presets.filter((p) => !p.is_builtin);

  const renderPreset = (preset: TuningPreset) => (
    <div
      key={preset.preset_id}
      className={`p-4 rounded-lg border transition-colors ${
        activePresetId === preset.preset_id
          ? "border-blue-500 dark:border-blue-400 bg-blue-50 dark:bg-blue-900/20"
          : "border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900"
      }`}
    >
      <div className="flex items-start justify-between">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-zinc-900 dark:text-white">
            {preset.name}
          </p>
          <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400 line-clamp-2">
            {preset.description}
          </p>
          <div className="mt-2 flex items-center gap-2">
            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400">
              {preset.domain}
            </span>
            {preset.is_builtin && (
              <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400">
                Built-in
              </span>
            )}
          </div>
        </div>
        <button
          onClick={() => onApply(preset.preset_id)}
          disabled={activePresetId === preset.preset_id}
          className={`ml-3 px-3 py-1.5 text-xs font-medium rounded transition-colors ${
            activePresetId === preset.preset_id
              ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400 cursor-default"
              : "bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-200 dark:hover:bg-zinc-700"
          }`}
        >
          {activePresetId === preset.preset_id ? "Active" : "Apply"}
        </button>
      </div>
    </div>
  );

  return (
    <div className="space-y-6">
      {builtin.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-3">
            Built-in Presets
          </h4>
          <div className="space-y-3">{builtin.map(renderPreset)}</div>
        </div>
      )}
      {custom.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-3">
            Custom Presets
          </h4>
          <div className="space-y-3">{custom.map(renderPreset)}</div>
        </div>
      )}
    </div>
  );
}
