"use client";

import { useCallback, useEffect, useState } from "react";
import { plugins } from "@/lib/api";
import type { PluginInfo, MarketplacePlugin } from "@/lib/api";

export default function PluginsPage() {
  const [installed, setInstalled] = useState<PluginInfo[]>([]);
  const [marketplace, setMarketplace] = useState<MarketplacePlugin[]>([]);
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState<"installed" | "browse">("installed");

  const [loadingInstalled, setLoadingInstalled] = useState(true);
  const [loadingMarketplace, setLoadingMarketplace] = useState(true);
  const [errorInstalled, setErrorInstalled] = useState<string | null>(null);
  const [errorMarketplace, setErrorMarketplace] = useState<string | null>(null);

  const [pendingAction, setPendingAction] = useState<string | null>(null);

  const fetchInstalled = useCallback(async () => {
    setLoadingInstalled(true);
    setErrorInstalled(null);
    try {
      const res = await plugins.list();
      setInstalled(res.plugins);
    } catch (e: unknown) {
      setErrorInstalled(e instanceof Error ? e.message : "Failed to load plugins");
    } finally {
      setLoadingInstalled(false);
    }
  }, []);

  const fetchMarketplace = useCallback(
    async (query?: string) => {
      setLoadingMarketplace(true);
      setErrorMarketplace(null);
      try {
        const res = await plugins.marketplace(query);
        setMarketplace(res.plugins);
      } catch (e: unknown) {
        setErrorMarketplace(e instanceof Error ? e.message : "Failed to load marketplace");
      } finally {
        setLoadingMarketplace(false);
      }
    },
    []
  );

  useEffect(() => {
    fetchInstalled();
    fetchMarketplace();
  }, [fetchInstalled, fetchMarketplace]);

  useEffect(() => {
    const timer = setTimeout(() => {
      fetchMarketplace(search || undefined);
    }, 300);
    return () => clearTimeout(timer);
  }, [search, fetchMarketplace]);

  const handleInstall = async (pluginId: string) => {
    setPendingAction(pluginId);
    try {
      await plugins.install(pluginId);
      await Promise.all([fetchInstalled(), fetchMarketplace(search || undefined)]);
    } catch (e: unknown) {
      setErrorMarketplace(e instanceof Error ? e.message : "Install failed");
    } finally {
      setPendingAction(null);
    }
  };

  const handleUninstall = async (pluginId: string) => {
    setPendingAction(pluginId);
    try {
      await plugins.uninstall(pluginId);
      await Promise.all([fetchInstalled(), fetchMarketplace(search || undefined)]);
    } catch (e: unknown) {
      setErrorInstalled(e instanceof Error ? e.message : "Uninstall failed");
    } finally {
      setPendingAction(null);
    }
  };

  const handleRate = async (pluginId: string, rating: number) => {
    try {
      const res = await plugins.rate(pluginId, rating);
      setMarketplace((prev) =>
        prev.map((p) =>
          p.plugin_id === pluginId
            ? { ...p, rating: res.average, rating_count: res.count }
            : p
        )
      );
    } catch {
      // silently ignore rating failures
    }
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
          {loadingInstalled ? (
            <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-8 text-center">
              <p className="text-sm text-zinc-500">Loading installed plugins...</p>
            </div>
          ) : errorInstalled ? (
            <div className="bg-white dark:bg-zinc-900 rounded-lg border border-red-200 dark:border-red-800 p-8 text-center">
              <p className="text-sm text-red-600 dark:text-red-400">{errorInstalled}</p>
              <button
                onClick={fetchInstalled}
                className="mt-2 text-xs text-blue-600 dark:text-blue-400 hover:underline"
              >
                Retry
              </button>
            </div>
          ) : installed.length === 0 ? (
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
                    disabled={pendingAction === plugin.plugin_id}
                    className="ml-3 px-3 py-1.5 text-xs font-medium rounded-lg border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/10 transition-colors disabled:opacity-50"
                  >
                    {pendingAction === plugin.plugin_id ? "Working..." : "Uninstall"}
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
            {loadingMarketplace ? (
              <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-8 text-center">
                <p className="text-sm text-zinc-500">Loading marketplace...</p>
              </div>
            ) : errorMarketplace ? (
              <div className="bg-white dark:bg-zinc-900 rounded-lg border border-red-200 dark:border-red-800 p-8 text-center">
                <p className="text-sm text-red-600 dark:text-red-400">{errorMarketplace}</p>
                <button
                  onClick={() => fetchMarketplace(search || undefined)}
                  className="mt-2 text-xs text-blue-600 dark:text-blue-400 hover:underline"
                >
                  Retry
                </button>
              </div>
            ) : marketplace.length === 0 ? (
              <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-8 text-center">
                <p className="text-sm text-zinc-500">No plugins found.</p>
              </div>
            ) : (
              marketplace.map((plugin) => (
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
                      <div className="mt-2 flex items-center gap-1">
                        {[1, 2, 3, 4, 5].map((star) => (
                          <button
                            key={star}
                            onClick={() => handleRate(plugin.plugin_id, star)}
                            className="text-sm text-zinc-300 dark:text-zinc-600 hover:text-amber-400 dark:hover:text-amber-400 transition-colors"
                            title={`Rate ${star} star${star !== 1 ? "s" : ""}`}
                          >
                            ★
                          </button>
                        ))}
                        <span className="ml-1 text-xs text-zinc-400">Rate</span>
                      </div>
                    </div>
                    {plugin.installed ? (
                      <span className="ml-3 px-3 py-1.5 text-xs font-medium rounded-lg bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400">
                        Installed
                      </span>
                    ) : (
                      <button
                        onClick={() => handleInstall(plugin.plugin_id)}
                        disabled={pendingAction === plugin.plugin_id}
                        className="ml-3 px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-50"
                      >
                        {pendingAction === plugin.plugin_id ? "Installing..." : "Install"}
                      </button>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
