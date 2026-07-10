"use client";

import { useEffect, useState } from "react";
import type { PluginInfo, MarketplacePlugin } from "@/lib/api";

const MOCK_INSTALLED: PluginInfo[] = [
  {
    plugin_id: "evalops-guardrails-core",
    name: "Guardrails Core",
    version: "0.2.1",
    author: "EvalOps",
    description: "Built-in guardrail checks for prompt injection, PII, and toxicity detection.",
    plugin_type: "guardrail",
    enabled: true,
    rating: { average: 4.5, count: 28 },
    usage: { total_checks: 15200, avg_latency_ms: 12 },
  },
  {
    plugin_id: "evalops-phi-filter",
    name: "Phi Filter",
    version: "0.1.0",
    author: "EvalOps",
    description: "Medical domain safety filter using the Phi-3 model for healthcare content.",
    plugin_type: "guardrail",
    enabled: false,
    rating: { average: 4.0, count: 7 },
    usage: { total_checks: 340, avg_latency_ms: 45 },
  },
];

const MOCK_MARKETPLACE: MarketplacePlugin[] = [
  {
    plugin_id: "evalops-faithfulness-scoring",
    name: "Faithfulness Scoring",
    version: "0.3.0",
    summary: "Advanced faithfulness evaluation using NLI models and claim decomposition.",
    author: "EvalOps",
    homepage: "https://github.com/evalops/faithfulness",
    installed: true,
    installed_version: "0.3.0",
    rating: 4.7,
    rating_count: 42,
    compatible: true,
  },
  {
    plugin_id: "evalops-citation-validator",
    name: "Citation Validator",
    version: "0.1.2",
    summary: "Verify that generated answers include proper citations to source documents.",
    author: "EvalOps",
    homepage: "https://github.com/evalops/citation-validator",
    installed: false,
    installed_version: "",
    rating: 4.3,
    rating_count: 15,
    compatible: true,
  },
  {
    plugin_id: "evalops-latency-tracker",
    name: "Latency Tracker",
    version: "0.2.0",
    summary: "Detailed latency breakdown and percentile tracking for pipeline steps.",
    author: "Community",
    homepage: "https://github.com/community/latency-tracker",
    installed: false,
    installed_version: "",
    rating: 3.9,
    rating_count: 8,
    compatible: true,
  },
  {
    plugin_id: "evalops-cost-monitor",
    name: "Cost Monitor",
    version: "0.1.0",
    summary: "Real-time cost tracking with budget alerts and usage analytics.",
    author: "Community",
    homepage: "",
    installed: false,
    installed_version: "",
    rating: 4.1,
    rating_count: 12,
    compatible: true,
  },
];

export default function PluginsPage() {
  const [installed, setInstalled] = useState<PluginInfo[]>(MOCK_INSTALLED);
  const [marketplace, setMarketplace] = useState<MarketplacePlugin[]>(MOCK_MARKETPLACE);
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState<"installed" | "browse">("installed");

  const filteredMarketplace = marketplace.filter(
    (p) =>
      p.name.toLowerCase().includes(search.toLowerCase()) ||
      p.summary.toLowerCase().includes(search.toLowerCase())
  );

  const handleInstall = (pluginId: string) => {
    setMarketplace((prev) =>
      prev.map((p) =>
        p.plugin_id === pluginId ? { ...p, installed: true } : p
      )
    );
  };

  const handleUninstall = (pluginId: string) => {
    setInstalled((prev) => prev.filter((p) => p.plugin_id !== pluginId));
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-zinc-900 dark:text-white">
          Plugins
        </h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
          Manage installed plugins and browse the marketplace
        </p>
      </div>

      <div className="flex items-center gap-1 bg-zinc-100 dark:bg-zinc-800 rounded-lg p-1 w-fit">
        {(["installed", "browse"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-1.5 text-xs font-medium rounded transition-colors ${
              activeTab === tab
                ? "bg-white dark:bg-zinc-700 text-zinc-900 dark:text-white shadow-sm"
                : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
            }`}
          >
            {tab === "installed" ? `Installed (${installed.length})` : "Browse Marketplace"}
          </button>
        ))}
      </div>

      {activeTab === "installed" && (
        <div className="space-y-3">
          {installed.length === 0 ? (
            <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-8 text-center">
              <p className="text-sm text-zinc-500">No plugins installed.</p>
            </div>
          ) : (
            installed.map((plugin) => (
              <div
                key={plugin.plugin_id}
                className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5"
              >
                <div className="flex items-start justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <h4 className="text-sm font-medium text-zinc-900 dark:text-white">
                        {plugin.name}
                      </h4>
                      <span className="text-xs font-mono text-zinc-400">
                        v{plugin.version}
                      </span>
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                          plugin.enabled
                            ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400"
                            : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500"
                        }`}
                      >
                        {plugin.enabled ? "Enabled" : "Disabled"}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                      {plugin.description}
                    </p>
                    <div className="mt-2 flex items-center gap-4 text-xs text-zinc-400">
                      <span>by {plugin.author}</span>
                      <span>Type: {plugin.plugin_type}</span>
                      {plugin.rating && (
                        <span>
                          ★ {Number((plugin.rating as Record<string, number>).average).toFixed(1)} ({(plugin.rating as Record<string, number>).count})
                        </span>
                      )}
                    </div>
                  </div>
                  <button
                    onClick={() => handleUninstall(plugin.plugin_id)}
                    className="ml-3 px-3 py-1.5 text-xs font-medium rounded-lg border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/10 transition-colors"
                  >
                    Uninstall
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {activeTab === "browse" && (
        <>
          <input
            type="text"
            placeholder="Search plugins..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full max-w-sm px-3 py-2 text-sm border border-zinc-200 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-900 text-zinc-900 dark:text-white placeholder:text-zinc-400"
          />
          <div className="space-y-3">
            {filteredMarketplace.map((plugin) => (
              <div
                key={plugin.plugin_id}
                className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5"
              >
                <div className="flex items-start justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <h4 className="text-sm font-medium text-zinc-900 dark:text-white">
                        {plugin.name}
                      </h4>
                      <span className="text-xs font-mono text-zinc-400">
                        v{plugin.version}
                      </span>
                      <span className="text-xs text-zinc-400">
                        ★ {plugin.rating.toFixed(1)} ({plugin.rating_count})
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                      {plugin.summary}
                    </p>
                    <p className="mt-1 text-xs text-zinc-400">by {plugin.author}</p>
                  </div>
                  {plugin.installed ? (
                    <span className="ml-3 px-3 py-1.5 text-xs font-medium rounded-lg bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400">
                      Installed
                    </span>
                  ) : (
                    <button
                      onClick={() => handleInstall(plugin.plugin_id)}
                      className="ml-3 px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors"
                    >
                      Install
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
