const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// Health check service
export interface HealthStatus {
  status: "healthy" | "degraded" | "unhealthy";
  timestamp: string;
  version?: string;
  uptime_seconds?: number;
}

export interface ConnectionStatus {
  connected: boolean;
  lastCheck: string | null;
  lastSuccess: string | null;
  failureCount: number;
}

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

// Diagnosis
export interface CounterfactualResult {
  intervention: {
    change_type: string;
    original_value: unknown;
    counterfactual_value: unknown;
    description: string;
  };
  counterfactual_score: number;
  improvement_delta: number;
  confidence: number;
  original_step_scores: Record<string, number>;
  counterfactual_step_scores: Record<string, number>;
}

export interface CounterfactualResponse {
  report_id: string;
  trace_id: string;
  original_score: number;
  results: CounterfactualResult[];
  best_intervention: { change_type: string; description: string } | null;
  best_delta: number;
}

export interface Recommendation {
  [key: string]: unknown;
}

export interface RecommendationResponse {
  trace_id: string;
  recommendations: Recommendation[];
  total: number;
}

export interface TrendDataPoint {
  period: string;
  count: number;
  failure_modes: Record<string, number>;
}

export interface TrendsResponse {
  trend: string;
  confidence: number;
  data_points: TrendDataPoint[];
  total_failures: number;
}

// Cost analysis
export interface CostBucket {
  label: string;
  total_cost_usd: number;
  entry_count: number;
  avg_cost_usd: number;
  avg_latency_ms: number;
  total_tokens: number;
}

export interface CostForecast {
  period_start: string;
  period_end: string;
  projected_cost_usd: number;
  confidence: number;
}

export interface CostAnomaly {
  entry_id: string;
  pipeline_id: string;
  model: string;
  cost_usd: number;
  expected_cost_usd: number;
  deviation_ratio: number;
}

export interface CostReport {
  total_cost_usd: number;
  total_entries: number;
  period_start: string | null;
  period_end: string | null;
  by_pipeline: CostBucket[];
  by_model: CostBucket[];
  by_user: CostBucket[];
  daily_costs: Record<string, unknown>[];
  forecasts: CostForecast[];
  anomalies: CostAnomaly[];
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

let connectionStatus: ConnectionStatus = {
  connected: false,
  lastCheck: null,
  lastSuccess: null,
  failureCount: 0,
};

let healthPollingInterval: ReturnType<typeof setInterval> | null = null;
const healthCheckPromises: Record<string, Promise<HealthStatus>> = {};

export function getConnectionStatus(): ConnectionStatus {
  return { ...connectionStatus };
}

export function startHealthPolling(onStatusChange?: (status: ConnectionStatus) => void) {
  if (healthPollingInterval) return;
  
  const updateStatus = (status: ConnectionStatus) => {
    connectionStatus = status;
    onStatusChange?.(status);
  };
  
  const checkHealth = async () => {
    const startTime = Date.now();
    const controller = new AbortController();
    
    // Timeout after 10 seconds
    setTimeout(() => controller.abort(), 10000);
    
    try {
      const res = await fetch(`${API_BASE}/health`, { signal: controller.signal });
      const data = await res.json();
      
      const timestamp = new Date().toISOString();
      updateStatus({
        connected: true,
        lastCheck: timestamp,
        lastSuccess: timestamp,
        failureCount: 0,
      });
    } catch (error) {
      const timestamp = new Date().toISOString();
      updateStatus({
        connected: false,
        lastCheck: timestamp,
        lastSuccess: connectionStatus.lastSuccess,
        failureCount: connectionStatus.failureCount + 1,
      });
    }
  };
  
  // Initial check
  checkHealth();
  
  healthPollingInterval = setInterval(() => {
    checkHealth();
  }, 30000); // Poll every 30 seconds
  
  return () => {
    if (healthPollingInterval) {
      clearInterval(healthPollingInterval);
      healthPollingInterval = null;
    }
  };
}

export function stopHealthPolling() {
  if (healthPollingInterval) {
    clearInterval(healthPollingInterval);
    healthPollingInterval = null;
  }
}

async function request<T>(path: string, options?: RequestInit, retries = 0): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30000);
  
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...options?.headers },
      ...options,
      signal: controller.signal,
    });
    
    clearTimeout(timeoutId);
    
    if (!res.ok) {
      throw new ApiError(res.status, await res.text());
    }
    
    // Update connection status to success
    if (connectionStatus.connected || connectionStatus.failureCount > 0) {
      const timestamp = new Date().toISOString();
      connectionStatus = {
        ...connectionStatus,
        connected: true,
        lastCheck: timestamp,
        lastSuccess: timestamp,
        failureCount: 0,
      };
    }
    
    return res.json() as Promise<T>;
  } catch (error) {
    clearTimeout(timeoutId);
    
    // Update connection status to failure
    if (connectionStatus.connected || connectionStatus.failureCount === 0) {
      const timestamp = new Date().toISOString();
      connectionStatus = {
        connected: false,
        lastCheck: timestamp,
        lastSuccess: connectionStatus.lastSuccess,
        failureCount: connectionStatus.failureCount + 1,
      };
    }
    
    // Retry logic for certain errors
    if (retries < 3 && (error instanceof ApiError ? error.status >= 500 : true)) {
      await new Promise(resolve => setTimeout(resolve, Math.pow(2, retries) * 1000));
      return request<T>(path, options, retries + 1);
    }
    
    throw error;
  }
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
  run: (body: { trajectory_id: string; metrics: string[] }[]) =>
    request<EvalResult>("/evals", { method: "POST", body: JSON.stringify(body) }),
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
  rate: (pluginId: string, rating: number) =>
    request<{ average: number; count: number }>(`/plugins/${pluginId}/rate`, {
      method: "POST",
      body: JSON.stringify({ rating }),
    }),
};

// Diagnosis
export const diagnosis = {
  counterfactual: (body: { trace_id?: string; trajectory?: Record<string, unknown> }) =>
    request<CounterfactualResponse>("/diagnosis/counterfactual", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  recommendations: (traceId: string) =>
    request<RecommendationResponse>(`/diagnosis/recommendations/${traceId}`),
  trends: (timeWindowDays = 30, bucketDays = 1) =>
    request<TrendsResponse>(
      `/diagnosis/trends?time_window_days=${timeWindowDays}&bucket_days=${bucketDays}`,
    ),
};

// Cost analysis
export const costs = {
  report: (opts: {
    pipelineId?: string;
    model?: string;
    userId?: string;
    days?: number;
    forecastDays?: number;
  } = {}) => {
    const params = new URLSearchParams();
    if (opts.pipelineId) params.set("pipeline_id", opts.pipelineId);
    if (opts.model) params.set("model", opts.model);
    if (opts.userId) params.set("user_id", opts.userId);
    if (opts.days !== undefined) params.set("days", String(opts.days));
    if (opts.forecastDays !== undefined) params.set("forecast_days", String(opts.forecastDays));
    const qs = params.toString();
    return request<CostReport>(`/optimize/costs${qs ? `?${qs}` : ""}`);
  },
};
