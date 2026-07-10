const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Types matching backend schemas
// ---------------------------------------------------------------------------

export type PipelineStatus = "draft" | "active" | "running" | "completed" | "failed";
export type TraceStatus = "pending" | "running" | "completed" | "failed";
export type SweepStatus = "pending" | "running" | "completed" | "failed";

export interface Pipeline {
  id: string;
  name: string;
  description: string;
  config: Record<string, unknown>;
  status: PipelineStatus;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface PipelineListResponse {
  pipelines: Pipeline[];
  total: number;
  page: number;
  page_size: number;
}

export interface PipelineCreate {
  name: string;
  description?: string;
  config?: Record<string, unknown>;
  tags?: string[];
}

export interface PipelineRunResponse {
  run_id: string;
  pipeline_id: string;
  status: string;
  started_at: string;
}

export interface TraceStep {
  step_id: number;
  step_type: string;
  input_text: string;
  output_text: string;
  tokens_used: number;
  cost_usd: number;
  duration_ms: number;
  status: string;
  metadata: Record<string, unknown>;
}

export interface Trace {
  id: string;
  pipeline_id: string;
  query: string;
  status: TraceStatus;
  steps: TraceStep[];
  total_tokens: number;
  total_cost_usd: number;
  started_at: string;
  completed_at: string | null;
  metadata: Record<string, unknown>;
}

export interface TraceListResponse {
  traces: Trace[];
  total: number;
  page: number;
  page_size: number;
}

export interface BlameAttribution {
  trace_id: string;
  failure_step: number;
  failure_type: string;
  confidence: number;
  root_cause: string;
  contributing_factors: string[];
  suggested_fixes: string[];
}

export interface EvalResult {
  id: string;
  trajectory_id: string;
  scores: Record<string, number>;
  aggregate_score: number;
  metric_details: Record<string, unknown>[];
  status: string;
  created_at: string;
}

export interface EvalCompareResponse {
  eval_a: EvalResult;
  eval_b: EvalResult;
  score_diffs: Record<string, number>;
  winner: string | null;
}

export interface SweepRequest {
  pipeline_id: string;
  search_space: Record<string, unknown>;
  objective?: string;
  n_trials?: number;
  timeout_seconds?: number;
}

export interface SweepStatusResponse {
  sweep_id: string;
  pipeline_id: string;
  status: SweepStatus;
  trials_completed: number;
  best_value: number | null;
  best_params: Record<string, unknown>;
  started_at: string;
  estimated_completion: string | null;
}

export interface ParetoPoint {
  params: Record<string, unknown>;
  objectives: Record<string, number>;
  rank: number;
}

export interface ParetoResponse {
  sweep_id: string;
  frontier: ParetoPoint[];
  total_points: number;
}

export interface MetricPreference {
  name: string;
  enabled: boolean;
  weight: number;
}

export interface FilterPreference {
  name: string;
  enabled: boolean;
  threshold: number;
  priority: number;
}

export interface OptimizationConstraints {
  max_cost_usd: number | null;
  min_quality: number | null;
  max_latency_ms: number | null;
}

export interface OptimizationPreferences {
  objective: "cost" | "quality" | "latency" | "balanced";
  constraints: OptimizationConstraints;
  max_trials: number;
  max_duration_seconds: number;
}

export interface UserPreferences {
  id: string;
  user_id: string;
  domain: string;
  metrics: MetricPreference[];
  filters: FilterPreference[];
  optimization: OptimizationPreferences;
  created_at: string;
  updated_at: string;
}

export interface TuningPreset {
  preset_id: string;
  name: string;
  description: string;
  domain: string;
  preferences: UserPreferences;
  is_builtin: boolean;
}

export interface PluginInfo {
  plugin_id: string;
  name: string;
  version: string;
  author: string;
  description: string;
  plugin_type: string;
  enabled: boolean;
  rating: Record<string, unknown>;
  usage: Record<string, unknown>;
}

export interface MarketplacePlugin {
  plugin_id: string;
  name: string;
  version: string;
  summary: string;
  author: string;
  homepage: string;
  installed: boolean;
  installed_version: string;
  rating: number;
  rating_count: number;
  compatible: boolean;
}

export interface PluginInstallResponse {
  success: boolean;
  plugin_id: string;
  message: string;
  version: string;
  signed: boolean;
}

// ---------------------------------------------------------------------------
// API Client
// ---------------------------------------------------------------------------

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(res.status, body);
  }
  return res.json() as Promise<T>;
}

// Pipelines
export const pipelines = {
  list: (page = 1, pageSize = 20, status?: string, tag?: string) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (status) params.set("status", status);
    if (tag) params.set("tag", tag);
    return request<PipelineListResponse>(`/pipelines?${params}`);
  },
  get: (id: string) => request<Pipeline>(`/pipelines/${id}`),
  create: (body: PipelineCreate) =>
    request<Pipeline>("/pipelines", { method: "POST", body: JSON.stringify(body) }),
  run: (id: string, configOverrides: Record<string, unknown> = {}) =>
    request<PipelineRunResponse>(`/pipelines/${id}/run`, {
      method: "POST",
      body: JSON.stringify({ config_overrides: configOverrides }),
    }),
  traces: (id: string, page = 1) =>
    request<{ pipeline_id: string; traces: Trace[]; total: number; page: number; page_size: number }>(
      `/pipelines/${id}/traces?page=${page}`
    ),
};

// Traces
export const traces = {
  list: (page = 1, pageSize = 20, pipelineId?: string, status?: string, minCost?: number, maxCost?: number) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (pipelineId) params.set("pipeline_id", pipelineId);
    if (status) params.set("status", status);
    if (minCost !== undefined) params.set("min_cost", String(minCost));
    if (maxCost !== undefined) params.set("max_cost", String(maxCost));
    return request<TraceListResponse>(`/traces?${params}`);
  },
  get: (id: string) => request<Trace>(`/traces/${id}`),
  blame: (id: string) => request<BlameAttribution>(`/traces/${id}/blame`),
};

// Evals
export const evals = {
  get: (id: string) => request<EvalResult>(`/evals/${id}`),
  compare: (evalA: string, evalB: string) =>
    request<EvalCompareResponse>(`/evals/compare?eval_a=${evalA}&eval_b=${evalB}`),
};

// Optimization
export const optimization = {
  startSweep: (body: SweepRequest) =>
    request<SweepStatusResponse>("/optimize/sweep", { method: "POST", body: JSON.stringify(body) }),
  status: (sweepId: string) => request<SweepStatusResponse>(`/optimize/status?sweep_id=${sweepId}`),
  pareto: (sweepId: string) => request<ParetoResponse>(`/optimize/pareto?sweep_id=${sweepId}`),
};

// Tuning
export const tuning = {
  getPreferences: (userId = "default", domain = "general") =>
    request<UserPreferences>(`/tuning/preferences?user_id=${userId}&domain=${domain}`),
  updatePreferences: (prefs: UserPreferences) =>
    request<UserPreferences>("/tuning/preferences", { method: "PUT", body: JSON.stringify(prefs) }),
  getMetrics: (userId = "default") =>
    request<Record<string, unknown>[]>(`/tuning/metrics?user_id=${userId}`),
  configureMetrics: (userId: string, metrics: MetricPreference[]) =>
    request<{ preferences: UserPreferences; warnings: string[] }>(`/tuning/metrics?user_id=${userId}`, {
      method: "PUT",
      body: JSON.stringify({ metrics }),
    }),
  getFilters: (userId = "default") =>
    request<Record<string, unknown>[]>(`/tuning/filters?user_id=${userId}`),
  configureFilters: (userId: string, filters: FilterPreference[]) =>
    request<{ preferences: UserPreferences; warnings: string[] }>(`/tuning/filters?user_id=${userId}`, {
      method: "PUT",
      body: JSON.stringify({ filters }),
    }),
  getOptimization: (userId = "default") =>
    request<Record<string, unknown>>(`/tuning/optimization?user_id=${userId}`),
  listPresets: () => request<TuningPreset[]>("/tuning/presets"),
  applyPreset: (presetId: string, userId = "default") =>
    request<{ preset_id: string; preferences: UserPreferences }>(
      `/tuning/presets/${presetId}/apply?user_id=${userId}`,
      { method: "POST" }
    ),
  getSmartDefaults: (userId = "default", domain = "general") =>
    request<Record<string, unknown>>(`/tuning/smart-defaults?user_id=${userId}&domain=${domain}`),
};

// Plugins
export const plugins = {
  list: (pluginType?: string, enabledOnly = false) => {
    const params = new URLSearchParams();
    if (pluginType) params.set("plugin_type", pluginType);
    if (enabledOnly) params.set("enabled_only", "true");
    return request<{ plugins: PluginInfo[]; total: number }>(`/plugins?${params}`);
  },
  marketplace: (search?: string) => {
    const params = search ? `?search=${encodeURIComponent(search)}` : "";
    return request<{ plugins: MarketplacePlugin[]; total: number }>(`/plugins/marketplace${params}`);
  },
  install: (pluginId: string, version?: string) =>
    request<PluginInstallResponse>("/plugins/install", {
      method: "POST",
      body: JSON.stringify({ plugin_id: pluginId, version }),
    }),
  uninstall: (pluginId: string) =>
    request<PluginInstallResponse>(`/plugins/${pluginId}`, { method: "DELETE" }),
};
