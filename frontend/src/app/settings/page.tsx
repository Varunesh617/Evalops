"use client";

import { useCallback, useEffect, useState } from "react";
import { settings } from "@/lib/api";
import type {
  ProviderInfo,
  ProviderKind,
  ProviderUpsert,
  SetActive,
  TestConn,
} from "@/lib/api";
import { useError } from "@/lib/error-context";
import { CardSkeleton } from "@/components/LoadingSkeleton";

const PROVIDER_KINDS: ProviderKind[] = [
  "openai",
  "ollama",
  "anthropic",
  "openrouter",
  "custom",
];

const DEFAULT_BASE_URLS: Record<ProviderKind, string> = {
  openai: "https://api.openai.com/v1",
  ollama: "http://localhost:11434",
  anthropic: "https://api.anthropic.com",
  openrouter: "https://openrouter.ai/api/v1",
  custom: "",
};

interface FormState {
  name: string;
  kind: ProviderKind;
  base_url: string;
  api_key: string;
  default_model: string;
  is_default: boolean;
}

const EMPTY_FORM: FormState = {
  name: "",
  kind: "openai",
  base_url: DEFAULT_BASE_URLS.openai,
  api_key: "",
  default_model: "",
  is_default: false,
};

export default function SettingsPage() {
  const { showToast } = useError();

  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [activeProvider, setActiveProvider] = useState<string>("");
  const [llmEnabled, setLlmEnabled] = useState<boolean>(false);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [editing, setEditing] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [pendingAction, setPendingAction] = useState<string | null>(null);

  const fetchProviders = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await settings.listProviders();
      setProviders(res.providers);
      setActiveProvider(res.active_provider);
      setLlmEnabled(res.llm_enabled);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load providers");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  const handleKindChange = (kind: ProviderKind) => {
    setForm((prev) => ({
      ...prev,
      kind,
      base_url: DEFAULT_BASE_URLS[kind] || prev.base_url,
    }));
  };

  const handleEdit = (provider: ProviderInfo) => {
    setEditing(provider.name);
    setForm({
      name: provider.name,
      kind: provider.kind,
      base_url: provider.base_url,
      api_key: "",
      default_model: provider.default_model,
      is_default: provider.is_default,
    });
  };

  const handleReset = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
  };

  const handleSave = async () => {
    if (!form.name.trim()) {
      showToast("Provider name is required", "error");
      return;
    }
    if (!form.base_url.trim()) {
      showToast("Base URL is required", "error");
      return;
    }
    setSaving(true);
    try {
      const payload: ProviderUpsert = {
        name: form.name.trim(),
        kind: form.kind,
        base_url: form.base_url.trim(),
        default_model: form.default_model.trim(),
        is_default: form.is_default,
      };
      if (form.api_key.trim()) payload.api_key = form.api_key.trim();
      await settings.addProvider(payload);
      showToast(
        editing ? "Provider updated" : "Provider added",
        "success",
      );
      handleReset();
      await fetchProviders();
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : "Failed to save provider", "error");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (name: string) => {
    setPendingAction(name);
    try {
      await settings.deleteProvider(name);
      showToast("Provider deleted", "success");
      if (editing === name) handleReset();
      await fetchProviders();
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : "Failed to delete provider", "error");
    } finally {
      setPendingAction(null);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      const req: TestConn = {
        name: editing ?? undefined,
        kind: form.kind,
        base_url: form.base_url.trim() || undefined,
        model: form.default_model.trim() || undefined,
      };
      if (form.api_key.trim()) req.api_key = form.api_key.trim();
      const res = await settings.testConnection(req);
      if (res.ok) {
        showToast(
          res.model ? `Connection OK (model: ${res.model})` : "Connection OK",
          "success",
        );
      } else {
        showToast(res.error || "Connection failed", "error");
      }
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : "Connection test failed", "error");
    } finally {
      setTesting(false);
    }
  };

  const handleSetActive = async (name: string) => {
    setPendingAction(name);
    try {
      const req: SetActive = { name, llm_enabled: llmEnabled };
      await settings.setActive(req);
      setActiveProvider(name);
      showToast(`Active provider set to ${name}`, "success");
      await fetchProviders();
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : "Failed to set active provider", "error");
    } finally {
      setPendingAction(null);
    }
  };

  const handleToggleLlm = async (enabled: boolean) => {
    setLlmEnabled(enabled);
    try {
      const req: SetActive = { name: activeProvider, llm_enabled: enabled };
      await settings.setActive(req);
      showToast(`LLM ${enabled ? "enabled" : "disabled"}`, "success");
    } catch (e: unknown) {
      setLlmEnabled(!enabled);
      showToast(e instanceof Error ? e.message : "Failed to toggle LLM", "error");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-zinc-900 dark:text-white">
          Settings
        </h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
          Configure LLM providers, test connections, and set the active provider
        </p>
      </div>

      <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-medium text-zinc-900 dark:text-white">
              LLM Engine
            </h3>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
              Enable LLM-backed features across EvalOps
            </p>
          </div>
          <button
            onClick={() => handleToggleLlm(!llmEnabled)}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
              llmEnabled
                ? "bg-emerald-600"
                : "bg-zinc-300 dark:bg-zinc-700"
            }`}
            aria-pressed={llmEnabled}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                llmEnabled ? "translate-x-6" : "translate-x-1"
              }`}
            />
          </button>
        </div>
      </div>

      <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5">
        <h3 className="text-sm font-medium text-zinc-900 dark:text-white mb-4">
          {editing ? `Edit Provider: ${editing}` : "Add Provider"}
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-1">
            <label className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
              Name
            </label>
            <input
              type="text"
              value={form.name}
              disabled={editing !== null}
              onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
              placeholder="my-openai"
              className="w-full px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-900 text-zinc-900 dark:text-white placeholder:text-zinc-400 disabled:opacity-60"
            />
          </div>

          <div className="space-y-1">
            <label className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
              Kind
            </label>
            <select
              value={form.kind}
              onChange={(e) => handleKindChange(e.target.value as ProviderKind)}
              className="w-full px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-900 text-zinc-900 dark:text-white"
            >
              {PROVIDER_KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1 md:col-span-2">
            <label className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
              Base URL
            </label>
            <input
              type="text"
              value={form.base_url}
              onChange={(e) => setForm((p) => ({ ...p, base_url: e.target.value }))}
              placeholder="https://api.openai.com/v1"
              className="w-full px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-900 text-zinc-900 dark:text-white placeholder:text-zinc-400 font-mono"
            />
          </div>

          <div className="space-y-1 md:col-span-2">
            <label className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
              API Key {editing && "(leave blank to keep existing)"}
            </label>
            <input
              type="password"
              value={form.api_key}
              onChange={(e) => setForm((p) => ({ ...p, api_key: e.target.value }))}
              placeholder="sk-..."
              className="w-full px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-900 text-zinc-900 dark:text-white placeholder:text-zinc-400 font-mono"
            />
          </div>

          <div className="space-y-1">
            <label className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
              Default Model
            </label>
            <input
              type="text"
              value={form.default_model}
              onChange={(e) => setForm((p) => ({ ...p, default_model: e.target.value }))}
              placeholder="gpt-4o"
              className="w-full px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-900 text-zinc-900 dark:text-white placeholder:text-zinc-400"
            />
          </div>

          <div className="flex items-end">
            <label className="flex items-center gap-2 text-sm text-zinc-700 dark:text-zinc-300">
              <input
                type="checkbox"
                checked={form.is_default}
                onChange={(e) => setForm((p) => ({ ...p, is_default: e.target.checked }))}
                className="h-4 w-4 rounded border-zinc-300 dark:border-zinc-600"
              />
              Set as default
            </label>
          </div>
        </div>

        <div className="flex items-center gap-2 mt-4">
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-50"
          >
            {saving ? "Saving..." : editing ? "Update Provider" : "Add Provider"}
          </button>
          <button
            onClick={handleTest}
            disabled={testing}
            className="px-4 py-2 text-sm font-medium rounded-lg border border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors disabled:opacity-50"
          >
            {testing ? "Testing..." : "Test Connection"}
          </button>
          {editing && (
            <button
              onClick={handleReset}
              className="px-4 py-2 text-sm font-medium rounded-lg text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 transition-colors"
            >
              Cancel
            </button>
          )}
        </div>
      </div>

      <div className="space-y-3">
        <h3 className="text-sm font-medium text-zinc-900 dark:text-white">
          Providers
        </h3>
        {loading ? (
          <>
            <CardSkeleton />
            <CardSkeleton />
          </>
        ) : error ? (
          <div className="bg-white dark:bg-zinc-900 rounded-lg border border-red-200 dark:border-red-800 p-8 text-center">
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
            <button
              onClick={fetchProviders}
              className="mt-2 text-xs text-blue-600 dark:text-blue-400 hover:underline"
            >
              Retry
            </button>
          </div>
        ) : providers.length === 0 ? (
          <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-8 text-center">
            <p className="text-sm text-zinc-500">No providers configured.</p>
          </div>
        ) : (
          providers.map((provider) => {
            const isActive = provider.name === activeProvider;
            return (
              <div
                key={provider.name}
                className={`bg-white dark:bg-zinc-900 rounded-lg border p-5 ${
                  isActive
                    ? "border-emerald-300 dark:border-emerald-700"
                    : "border-zinc-200 dark:border-zinc-800"
                }`}
              >
                <div className="flex items-start justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <h4 className="text-sm font-medium text-zinc-900 dark:text-white">
                        {provider.name}
                      </h4>
                      <span className="text-xs font-mono text-zinc-400">
                        {provider.kind}
                      </span>
                      {isActive && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400">
                          Active
                        </span>
                      )}
                      {provider.is_default && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-500">
                          Default
                        </span>
                      )}
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-400">
                      <span className="font-mono break-all">{provider.base_url}</span>
                      <span>Model: {provider.default_model || "—"}</span>
                      <span>
                        API Key:{" "}
                        <span
                          className={
                            provider.api_key_state === "set"
                              ? "text-emerald-600 dark:text-emerald-400"
                              : "text-zinc-500"
                          }
                        >
                          {provider.api_key_state === "set" ? "set" : "unset"}
                        </span>
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 ml-3">
                    {!isActive && (
                      <button
                        onClick={() => handleSetActive(provider.name)}
                        disabled={pendingAction === provider.name}
                        className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-50"
                      >
                        {pendingAction === provider.name ? "Working..." : "Set Active"}
                      </button>
                    )}
                    <button
                      onClick={() => handleEdit(provider)}
                      className="px-3 py-1.5 text-xs font-medium rounded-lg border border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => handleDelete(provider.name)}
                      disabled={pendingAction === provider.name}
                      className="px-3 py-1.5 text-xs font-medium rounded-lg border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/10 transition-colors disabled:opacity-50"
                    >
                      {pendingAction === provider.name ? "Working..." : "Delete"}
                    </button>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
