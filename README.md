# EvalOps — Unified Full-Pipeline LLM Evaluation & Optimization Platform

> **Status:** Python 3.13 · FastAPI · Next.js 16 (React 19) · Optuna · MongoDB durable mirror

EvalOps observes and scores any LLM-backed pipeline — local or cloud — by watching
its **input/output boundary**, then attribute failures and optimize configuration
through Bayesian search. It is behavioral observability for LLM systems, not
black-box guessing.

---

## Table of Contents

- [Overview / Why EvalOps](#overview--why-evalops)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Supported LLM Backends](#supported-llm-backends)
- [Local LLM Models — Visibility & Telemetry](#local-llm-models--visibility--telemetry)
- [Quick Start](#quick-start)
- [Evaluation Workflow](#evaluation-workflow)
- [Configuration & Conventions](#configuration--conventions)
- [Project Structure](#project-structure)
- [Development](#development)
- [License](#license)

---

## Overview / Why EvalOps

Modern LLM pipelines are multi-stage systems: retrieve context, rerank it, reason
over it, apply guardrails, then generate. When the final answer is wrong or
expensive, the question is never "is the model bad?" — it is "**which step
broke, and why?**"

EvalOps answers that by treating the pipeline as a first-class object:

- It executes the full `Retrieve → Rerank → Reason → Guardrail → Generate`
  pipeline and records a **trajectory** — a step-by-step trace with latency,
  token usage, and payloads.
- It runs a pluggable **eval engine** over that trajectory with six built-in
  metrics (faithfulness, context relevance, cost efficiency, tool-call accuracy,
  trajectory coherence, guardrail false-positive rate).
- It performs **blame attribution** — root-cause detection, cascade analysis,
  and per-step timings — so you know whether a bad answer came from empty
  retrieval, a misrank, an overzealous guardrail, or a slow step blowing the
  latency budget.
- It sweeps pipeline **configuration** with Optuna (Bayesian optimization) to find
  the quality/cost/latency sweet spot.
- The whole thing streams **live** to a Next.js dashboard over WebSockets and is
  mirrored to MongoDB for durable, queryable history.

The core principle: **evaluate the model by observing its I/O boundary.** EvalOps
does not need white-box access to a model's weights. It measures what the model
*does* — given this query and context, what does it return, how long did it take,
and how much did it cost? That makes it equally effective for OpenAI, Anthropic,
and a model you run on your own GPU.

---

## Key Features

- **Pipeline tracing** — every step (retrieve, rerank, reason, guardrail,
  generate) is traced with per-step latency (`latency_ms`), token usage
  (`prompt/completion/total`), status, and payload.
- **Eval engine with 6 metrics** — `faithfulness`, `context_relevance`,
  `trajectory_coherence`, `tool_call_accuracy`, `guardrail_fp_rate`,
  `cost_efficiency`. Metrics run in parallel (offloaded to a thread pool so the
  event loop stays responsive) and support batch evaluation with bounded
  concurrency.
- **Blame attribution** — heuristic root-cause classification (`timeout`,
  `low_score`, `guardrail_violation`, `empty_result`, `token_limit`, `exception`,
  `slow_step`, `degradation`), a **cascade chain** showing how a failure at step N
  propagates to N+1, **counterfactual analysis**, remediation suggestions, and an
  optional LLM-as-judge rubric. Includes `SLOW_STEP` detection vs. per-step
  latency budgets and a `step_timings` breakdown.
- **Optuna config sweep** — `ConfigSweeper` runs N Bayesian trials
  (`TPESampler` + `MedianPruner`), scoring each on a weighted
  quality/cost/latency composite, and reports the best `PipelineConfig` plus
  parameter importances.
- **Plugin ecosystem** — model-agnostic SDK with four extension points:
  `MetricPlugin`, `FilterPlugin`, `OptimizerPlugin`, `IntegrationPlugin`. Third
  parties register via entry points (`evalops.plugins`).
- **Live WebSocket dashboard** — `/ws/traces` streams trace events in real time
  with topic-based filtering (by `pipeline_id`, `trace_id`, or `event_types`).
- **MongoDB durable mirror** — opt-in (`MONGODB_URL`) best-effort mirror of
  in-memory repository writes, with a monotonic `serial_no` and BSON `ISODate`
  timestamps (`created_at`/`updated_at`).
- **Local LLM telemetry** — when the evaluated model is self-hosted, EvalOps
  attaches live backend visibility (model list, model architecture, GPU stats)
  to each trace. See [below](#local-llm-models--visibility--telemetry).

---

## Architecture

### Backend (`backend/`, Python 3.13 / FastAPI)

| Module | Responsibility |
|--------|----------------|
| `core/pipeline.py` | `PipelineExecutor` orchestrates `Retrieve → Rerank → Reason → Guardrail → Generate`; records a `Trajectory` via the `Tracer`. `PipelineBuilder` provides a fluent API. |
| `core/llm_client.py` | `LLMClient` — async, OpenAI-compatible client; also speaks Anthropic `/v1/messages` via `provider="anthropic"`. |
| `core/local_model_stats.py` | `attach_local_stats` / `fetch_local_stats` / `fetch_model_details` / `fetch_gpu_stats` — fail-soft visibility into local backends. |
| `core/config.py` | `PipelineConfig` (+ per-step configs) and settings/enums. |
| `eval/engine.py` | `EvalEngine` — dispatches a `Trajectory` through configured metrics (parallel or sequential, batch-capable). |
| `eval/metrics/` | The 6 built-in metrics + `METRIC_REGISTRY` + `BaseMetric`. |
| `eval/blame_attribution.py` | `BlameAttributionEngine` → `BlameReport` (root cause, cascade, remediation, counterfactuals, timings). |
| `optimizer/config_sweeper.py` | `ConfigSweeper` — Optuna Bayesian sweep over the pipeline search space. |
| `diagnosis/` | Pipeline diagnosis tooling. |
| `guardrails/` | Guardrail filters and evaluation (referenced by `guardrail_fp_rate`, `FilterPlugin`). |
| `plugins/` | Plugin SDK (`sdk.py`), discovery, loader, registry, security, marketplace. |
| `db/` | `repositories.py` (in-memory source of truth) and `mongo_mirror.py` (opt-in durable mirror). |
| `api/` | FastAPI routers, including `websocket.py` (`/ws/traces`). |
| `tuning/` | Model/tuning configuration helpers. |

### Frontend (`frontend/`, Next.js 16 + React 19 + TypeScript, App Router)

Dashboard pages under `frontend/src/app/`:

- `evals/` — run and inspect evaluations.
- `traces/` — per-trace trajectory view.
- `cost-analysis/` — cost efficiency breakdown.
- `diagnosis/` — failure diagnosis / blame reports.
- `optimization/` — Optuna sweep results.
- `pipelines/` — pipeline definitions.
- `plugins/` — plugin registry / marketplace.
- `settings/` — configuration.
- `tuning/` — model tuning.

All API access goes through the typed client in `frontend/src/lib/api.ts`
(see `AGENTS.md` for frontend conventions). `NEXT_PUBLIC_API_URL` points the
frontend at the backend (default `http://localhost:8000`).

### Data Flow

```
query
  │
  ▼
PipelineExecutor  ──►  Trajectory (Retrieve → Rerank → Reason → Guardrail → Generate)
  │                        │  per-step latency / tokens / payload
  │                        ▼
  │                 EvalEngine  ──►  6 metrics  ──►  EvalResult
  │                        │
  │                        ▼
  │                 BlameAttributionEngine  ──►  BlameReport (root cause, cascade, timings)
  │
  ├──► Optuna ConfigSweeper (Bayesian config search) ──► best PipelineConfig
  │
  ├──► WebSocket (live trace stream)  ──►  Next.js Dashboard
  └──► MongoDB mirror (durable, opt-in via MONGODB_URL)
```

---

## Supported LLM Backends

EvalOps is **provider-agnostic**. The pipeline speaks the OpenAI Chat Completions
protocol (via `LLMClient`), so any OpenAI-compatible endpoint works out of the box:

- **OpenAI** — `https://api.openai.com/v1` (default).
- **OpenRouter** and other OpenAI-compatible gateways (Together, Groq, Fireworks,
  DeepInfra, …) — set `EVALOPS_LLM_BASE_URL`.
- **Anthropic Claude** — set `provider="anthropic"` (or
  `EVALOPS_LLM_PROVIDER=anthropic`); requests are translated to `/v1/messages`.
- **Local** — Ollama (`/v1`), vLLM, LM Studio, LocalAI via an OpenAI-compatible
  `base_url` such as `http://localhost:11434/v1`.

Connection resolution order: explicit constructor args → `EVALOPS_LLM_*`
environment variables → provider defaults. See the
[Local LLM telemetry](#local-llm-models--visibility--telemetry) section for what
extra visibility local models get.

---

## Local LLM Models — Visibility & Telemetry

EvalOps evaluates **local** models identically to cloud models — purely through
their I/O boundary. The same trajectory, the same six metrics, the same blame
attribution apply. On top of that, when the evaluated endpoint is detected as
*local* (host is `localhost` / `127.0.0.1` / `0.0.0.0`), EvalOps adds **backend
telemetry** to the trace so you can see *where the local effort went*.

What it collects:

- **Model list** — probes Ollama's unauthenticated `GET {origin}/api/tags`
  (non-Ollama local servers fall back to a generic liveness probe).
- **Model architecture** — for each model, hits Ollama `POST {origin}/api/show`
  and captures: `parameter_size`, `quantization_level`, `context_length`,
  `format`, `family`, and `layer_count`.
- **GPU stats** — runs `nvidia-smi` (in a thread) for `gpu_name`,
  `vram_used_mib`, `vram_total_mib`, and `gpu_utilization_pct`.

### Honest limitation

This telemetry is **behavioral and host-level only**. EvalOps does **not** inspect
model weights, activations, attention patterns, or logits — the model process
remains a black box at the neural level. Closing that gap (e.g. circuit analysis,
activation probing) would require *in-process* interpretability tooling such as
`transformerlens` or `nnsight`, which is **out of scope** for this platform. What
you get is: which model served the request, its architecture, and the GPU it ran
on — not how it "thought."

### Example `local_backend` payload

The telemetry is attached to the step payload as `local_backend` (a no-op for
cloud endpoints) and looks like:

```json
{
  "source": "local",
  "kind": "ollama",
  "available": true,
  "models": ["llama3", "qwen2.5:7b", "mistral"],
  "latency_probe_ms": 4.2,
  "error": null,
  "model_details": {
    "name": "llama3",
    "parameter_size": "8.0B",
    "quantization_level": "Q4_K_M",
    "context_length": 8192,
    "format": "gguf",
    "family": "llama",
    "layer_count": 32,
    "details_ok": true
  },
  "gpu_stats": {
    "available": true,
    "gpu_name": "NVIDIA RTX 4090",
    "vram_used_mib": 9120,
    "vram_total_mib": 24564,
    "gpu_utilization_pct": 73.0,
    "error": null
  }
}
```

### Fail-soft by design

Every telemetry function swallows network/process errors and returns a structured
dict (with `available: false` and an `error` field) instead of raising. Attaching
local stats **never blocks or breaks the pipeline** — if Ollama is absent or
`nvidia-smi` is not installed, the step still completes and the trace simply
carries less backend detail.

---

## Quick Start

The full stack runs with Docker Compose:

- Backend API: `http://localhost:8000`
- Frontend: `http://localhost:3000`
- Postgres: `:5432`
- Redis: `:6379`

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

### Environment variables

The judge/model under evaluation is configured via `EVALOPS_LLM_*`:

| Variable | Purpose |
|----------|---------|
| `EVALOPS_LLM_API_KEY` | API key for the LLM (judge + generation). |
| `EVALOPS_LLM_BASE_URL` | OpenAI-compatible base URL. |
| `EVALOPS_LLM_MODEL` | Default model name. |
| `EVALOPS_LLM_ENABLED` | Whether the LLM client is enabled (judge). |
| `MONGODB_URL` | **Enables the MongoDB durable mirror** (opt-in; omit to use in-memory only). |
| `DATABASE_URL` | SQL/Postgres connection (SQL models via SQLAlchemy + Alembic). |
| `NEXT_PUBLIC_API_URL` | Frontend → backend base URL (default `http://localhost:8000`). |

**For a local model (Ollama):**

```bash
export EVALOPS_LLM_BASE_URL=http://localhost:11434/v1
export EVALOPS_LLM_MODEL=llama3
export EVALOPS_LLM_API_KEY=ollama            # Ollama ignores the key, but it must be set
export EVALOPS_LLM_ENABLED=true
# Optional durable mirror:
export MONGODB_URL=mongodb://localhost:27017
```

Cloud example:

```bash
export EVALOPS_LLM_BASE_URL=https://api.openai.com/v1
export EVALOPS_LLM_MODEL=gpt-4o
export EVALOPS_LLM_API_KEY=sk-...
export EVALOPS_LLM_ENABLED=true
```

---

## Evaluation Workflow

1. **Run a pipeline** to produce a trajectory. The executor runs the default
   `Retrieve → Rerank → Reason → Guardrail → Generate` sequence (or a custom
   one built with `PipelineBuilder`) and returns a `Trajectory`.

2. **Evaluate** the trajectory. The eval engine dispatches it through the
   configured metrics:

   ```bash
   curl -X POST http://localhost:8000/evals \
     -H "Content-Type: application/json" \
     -d '{"trajectory_id": "<id>", "metrics": ["faithfulness", "cost_efficiency"]}'
   ```

   In code:

   ```python
   from backend.eval.engine import EvalEngine

   engine = EvalEngine(metrics=["faithfulness", "context_relevance"])
   result = await engine.run(trajectory)
   print(result.aggregate_score)   # see EvalResult / MetricResult
   ```

3. **Get blame attribution** for a trace:

   ```bash
   curl http://localhost:8000/traces/<trace_id>/blame
   ```

   This returns a `BlameReport`: `root_cause_step`, `root_cause_mode`,
   `severity`, `cascade_chain`, `remediation`, `counterfactuals`, `step_timings`,
   and a `score` (1.0 = healthy, →0 = severe failure). `SLOW_STEP` is reported
   when a step exceeds its latency budget even if it succeeded.

4. **Sweep configuration** with Optuna to find the best quality/cost/latency
   trade-off:

   ```python
   from backend.optimizer.config_sweeper import ConfigSweeper

   sweeper = ConfigSweeper(eval_fn=my_eval_fn, n_trials=50)
   result = await sweeper.run()
   print(result.best_config, result.best_composite_score)
   ```

   `config_sweeper.py` defines the search space across retrieval, reranker,
   agent, guardrail, and generator knobs, scoring each trial with a weighted
   composite (`quality 0.6 / cost 0.25 / latency 0.15`) and reporting parameter
   importances.

5. **Watch it live** — connect to `ws://localhost:8000/ws/traces` and send a
   subscription message to receive streamed trace events in the dashboard.

---

## Configuration & Conventions

Full contributor and architecture conventions live in **`AGENTS.md`** (required
reading before changing code). Highlights:

- **Storage is layered.** The in-memory repository is the source of truth for
  reads/queries used by the dashboard. The **MongoDB mirror is opt-in** — set
  `MONGODB_URL` and every write is also mirrored (best-effort, fire-and-forget)
  to a durable, queryable log with `serial_no` + `ISODate` timestamps. SQL models
  (Postgres/async SQLAlchemy + Alembic) are configured via `DATABASE_URL`.
- **Backend:** Python 3.13, FastAPI, Pydantic v2, async SQLAlchemy, `structlog`.
  Config/enums in `backend/core/config.py`; DB access via
  `backend/db/repositories.py`. Validate input at system boundaries; never log or
  commit secrets.
- **Frontend:** always use the typed API client (`frontend/src/lib/api.ts`),
  never `fetch` directly. `RootLayout` is a server component; interactive logic
  lives in `AppShell.tsx`. Nav links in `Sidebar.tsx`.

---

## Project Structure

```
evalops/
├── backend/            # Python 3.13 FastAPI service
│   ├── api/            # routers (incl. websocket.py)
│   ├── core/           # pipeline, llm_client, local_model_stats, config, tracer
│   ├── eval/           # engine.py, metrics/, blame_attribution.py, llm_judge
│   ├── optimizer/      # config_sweeper.py (Optuna)
│   ├── diagnosis/      # diagnosis tooling
│   ├── guardrails/     # guardrail filters + eval
│   ├── plugins/        # SDK, discovery, loader, registry, marketplace
│   ├── tuning/         # tuning helpers
│   ├── db/             # repositories.py, mongo_mirror.py
│   └── cli.py          # `evalops` entry point
├── frontend/           # Next.js 16 + React 19 + TypeScript (App Router)
│   └── src/app/        # evals, traces, cost-analysis, diagnosis, optimization,
│                       # pipelines, plugins, settings, tuning
├── tests/              # unit/, integration/, e2e/ (pytest)
├── docker/             # Dockerfile.backend, docker-compose.yml
├── alembic/            # SQL migrations
├── AGENTS.md           # contributor conventions
├── pyproject.toml
└── Jenkinsfile         # CI/CD (Docker + lint/type/test gates)
```

---

## Development

### Backend (FastAPI)

```bash
# from repo root
pip install -e ".[dev,plugins]"          # or: pip install -e ".[dev]"
python -m pytest -q                      # run the suite (target: all pass)
```

Ruff and mypy (strict) are configured in `pyproject.toml`:

```bash
ruff check backend                      # lint
mypy backend                            # type-check (strict)
```

Run the API locally (outside Docker):

```bash
uvicorn backend.api.main:app --reload --port 8000
```

### Frontend (Next.js 16)

```bash
cd frontend
npm ci
npx tsc --noEmit                        # type-check
npm run build                           # production build
npm run dev                             # dev server on :3000
```

> Note: `npm run lint` may report React 19 / Next 16 rule warnings
> (`react-hooks/set-state-in-effect`, `react-hooks/purity`) that also exist in
> older code and do **not** block `next build`. Do not regress beyond existing
> patterns.

### CI/CD

Deployment is via **Jenkins** (`Jenkinsfile`): preflight → install → parallel
lint/type-check (ruff, mypy, eslint, tsc) → pytest → `next build` → bandit →
build both Docker images. Lint/type/security stages are non-blocking (UNSTABLE);
tests and builds are hard gates. On `main`/`master`, deploy via
`docker compose -f docker/docker-compose.yml up -d --build` + a `/health` smoke
test.

---

## License

Released under the **MIT License**.
