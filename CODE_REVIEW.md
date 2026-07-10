# Code Review Report — EvalOps Backend

**Reviewer:** big-pickle (MoE Swarm Reviewer Agent)
**Date:** 2026-07-10
**Project:** EvalOps — Unified Full-Pipeline Evaluation & Optimization Platform
**Scope:** All 45 Python source files under `backend/`

---

## Summary

| Metric | Count |
|--------|-------|
| Files reviewed | 45 |
| Total lines reviewed | ~5,200 |
| **Critical issues** | **4** |
| **High issues** | **8** |
| **Medium issues** | **10** |
| **Low issues** | **7** |
| **Total findings** | **29** |

---

## Findings

---

### [CRITICAL] 1. CORS wildcard + credentials enabled — credential theft vector

- **File:** `backend/api/app.py`
- **Line:** 112–119
- **Issue:** `allow_origins=["*"]` combined with `allow_credentials=True` is a security vulnerability. Per the CORS spec, browsers reject credentialed requests when the origin is `*`. But more critically, this configuration is the textbook pattern for credential leakage — any origin can send authenticated requests to this API. An attacker on any domain can steal session data or impersonate users.
- **Suggestion:** Replace `["*"]` with an explicit allowlist loaded from environment variables, or remove `allow_credentials=True` if cookies/tokens aren't used.
- **Code:**
```python
# BEFORE (line 112-119)
application.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # ← DANGER
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Process-Time-Ms"],
)

# AFTER
import os
_allowed_origins = os.getenv("CORS_ORIGINS", "").split(",")
application.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=len(_allowed_origins) > 0 and _allowed_origins != [""],
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Process-Time-Ms"],
)
```

---

### [CRITICAL] 2. WebSocket double-accept crash

- **File:** `backend/api/websocket.py`
- **Line:** 28 and 93
- **Issue:** `trace_stream()` calls `await ws.accept()` at line 93, then immediately calls `manager.connect(ws, subscription)` which calls `await ws.accept()` again at line 28. Starlette will raise `WebSocketDisconnect` or an error on the second accept. This endpoint is completely broken.
- **Suggestion:** Remove the duplicate `ws.accept()` in either location. Since `ConnectionManager.connect` owns the accept, remove line 93.
- **Code:**
```python
# BEFORE (line 93-102)
async def trace_stream(ws: WebSocket) -> None:
    await ws.accept()                    # ← REMOVE THIS
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        subscription = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError):
        subscription = {}
    await manager.connect(ws, subscription)  # this calls accept() internally

# AFTER
async def trace_stream(ws: WebSocket) -> None:
    # Wait for subscription message before accepting (timeout 10s)
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        subscription = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError):
        subscription = {}
    await manager.connect(ws, subscription)  # accepts the connection
```

---

### [CRITICAL] 3. PipelineExecutor leaves trajectory un-finalized on exception

- **File:** `backend/core/pipeline.py`
- **Line:** 218–225
- **Issue:** When a step raises an exception, the code sets `result` then `raise`s at line 225. This causes the exception to propagate out of `execute()` without reaching `self.tracer.finish(trajectory)` at line 251. The trajectory is left in an unfinished state — `end_time` is `None`, `latency_ms` is `None`, and downstream consumers (blame attribution, scoring) will get incomplete data.
- **Suggestion:** Wrap the execution loop in try/finally, or catch and finalize the trajectory before re-raising.
- **Code:**
```python
# AFTER
async def execute(self, query: str) -> Trajectory:
    trajectory = self.tracer.start(pipeline_id=self.config.pipeline_id)
    ctx = PipelineContext(config=self.config, trajectory=trajectory, query=query)

    try:
        for pipeline_step in self.steps:
            async with self.tracer.step(trajectory, pipeline_step.name) as step:
                try:
                    result = await pipeline_step.execute(ctx)
                except Exception as exc:
                    result = {
                        "status": "failed",
                        "error": str(exc),
                        "error_type": type(exc).__qualname__,
                    }
                    step.payload["result"] = result
                    raise

                status = result.pop("status", "success")
                step.payload["result"] = result

                if "tokens" in result:
                    tok = result.pop("tokens")
                    step.tokens = TokenUsage(**tok)

                if status == "failed":
                    step.finish(
                        status=StepStatus.FAILED,
                        error=result.get("error"),
                        error_type=result.get("error_type"),
                    )
                    ctx.results[pipeline_step.name] = result
                    logger.error(
                        "step_failed",
                        step=pipeline_step.name,
                        error=result.get("error"),
                    )
                    break

                ctx.results[pipeline_step.name] = result
    finally:
        self.tracer.finish(trajectory)

    return trajectory
```

---

### [CRITICAL] 4. In-memory rate limiter is trivially bypassable and unbounded

- **File:** `backend/api/app.py`
- **Line:** 24–57
- **Issue:** Two problems:
  1. The rate limit key is derived from `X-Forwarded-For` (spoofable by any client) — an attacker can bypass rate limiting entirely by rotating this header.
  2. `_rate_buckets: dict[str, tuple[float, int]] = {}` grows without bound. An attacker making requests from many spoofed IPs will exhaust memory (DoS).
- **Suggestion:** Use a proper rate limiter (e.g., `slowapi` with Redis backend), or at minimum add TTL-based eviction and cap the dict size.
- **Code:**
```python
# Quick fix — add eviction in the middleware
class RateLimitMiddleware(BaseHTTPMiddleware):
    MAX_BUCKETS = 10_000

    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/health"):
            return await call_next(request)

        key = _rate_limit_key(request)
        now = time.monotonic()

        # Evict stale entries if too many
        if len(_rate_buckets) > self.MAX_BUCKETS:
            stale = [k for k, (ts, _) in _rate_buckets.items() if now - ts > RATE_WINDOW]
            for k in stale:
                del _rate_buckets[k]

        bucket = _rate_buckets.get(key)
        if bucket is None or now - bucket[0] > RATE_WINDOW:
            _rate_buckets[key] = (now, 1)
        elif bucket[1] >= RATE_LIMIT:
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded."})
        else:
            _rate_buckets[key] = (bucket[0], bucket[1] + 1)

        return await call_next(request)
```

---

### [HIGH] 5. Shared mutable class-level DEFAULT_STEPS list

- **File:** `backend/core/pipeline.py`
- **Line:** 185–191
- **Issue:** `DEFAULT_STEPS` is a mutable list defined at the class level. When `PipelineExecutor` is instantiated without custom steps, line 200 creates `list(self.DEFAULT_STEPS)` — a shallow copy. This is safe for now, but the class-level mutable default is a footgun. If anyone mutates it (e.g., `PipelineExecutor.DEFAULT_STEPS.append(...)`), all future instances are affected.
- **Suggestion:** Make it a tuple or a classmethod that returns a fresh list each time.
- **Code:**
```python
# BEFORE
DEFAULT_STEPS: list[PipelineStep] = [
    RetrieveStep(),
    RerankStep(),
    ReasonStep(),
    GuardrailStep(),
    GenerateStep(),
]

# AFTER
@classmethod
def _default_steps(cls) -> list[PipelineStep]:
    return [
        RetrieveStep(),
        RerankStep(),
        ReasonStep(),
        GuardrailStep(),
        GenerateStep(),
    ]

# And update line 200:
self.steps = steps if steps is not None else self._default_steps()
```

---

### [HIGH] 6. GuardrailStack fail_fast logic is dead code

- **File:** `backend/guardrails/stack.py`
- **Line:** 92–98
- **Issue:** The `fail_fast` branch at line 97 (`elif self.fail_fast and result.blocked:`) can never execute. When `result.blocked` is `True`, the `if result.blocked:` at line 95 already matches. The `elif` is only reached when `result.blocked` is `False`, so `result.blocked` in the condition is always `False`. The entire `fail_fast` feature is broken.
- **Suggestion:** Swap the order — check `fail_fast` first, then handle the blocked case.
- **Code:**
```python
# BEFORE (line 92-98)
for f in self.filters:
    result = f.check(input_text, context=context, output=output)
    results.append(result)
    if result.blocked:
        blocked_filters.extend(result.blocked_by)
    elif self.fail_fast and result.blocked:  # ← DEAD CODE
        break

# AFTER
for f in self.filters:
    result = f.check(input_text, context=context, output=output)
    results.append(result)
    if result.blocked:
        blocked_filters.extend(result.blocked_by)
        if self.fail_fast:
            break
```

---

### [HIGH] 7. StepScore name collision between two modules

- **File:** `backend/eval/trajectory_scorer.py:122` and `backend/eval/models.py:62`
- **Issue:** Both modules define a class named `StepScore` with completely different structures:
  - `trajectory_scorer.StepScore` (dataclass): fields `step_name`, `status`, `score`, `breakdown`
  - `models.StepScore` (Pydantic): fields `step_id`, `metric_name`, `score`, `details`, `breakdown`

  Any code that imports from both modules will shadow one. The `trajectory_scorer.py` file already imports from `backend.core.tracer` (not `backend.eval.models`), so it uses its own. But this is a maintainability trap.
- **Suggestion:** Rename the dataclass in `trajectory_scorer.py` to `ScorerStepResult` or `StepScoringBreakdown` to avoid ambiguity.
- **Code:**
```python
# In backend/eval/trajectory_scorer.py, rename:
@dataclass(slots=True)
class StepScoringBreakdown:  # was StepScore
    step_name: str
    status: StepStatus
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)
```

---

### [HIGH] 8. Pipeline run endpoint returns 202 but never executes

- **File:** `backend/api/routes/pipelines.py`
- **Line:** 94–123
- **Issue:** `POST /pipelines/{id}/run` returns HTTP 202 (Accepted) with a `run_id`, implying background execution. But the run is just stored in an in-memory dict with status `"queued"`. No background task, worker, or coroutine ever picks it up. The client will poll forever for a run that never completes.
- **Suggestion:** Either implement actual background execution (e.g., `asyncio.create_task` or a Celery task) or return 501 Not Implemented for this endpoint until the feature is built. At minimum, document the limitation clearly.
- **Code:**
```python
# Quick fix — explicit 501 until background execution is implemented
@router.post("/{pipeline_id}/run", response_model=PipelineRunResponse, status_code=202)
async def run_pipeline(pipeline_id: str, body: PipelineRunRequest) -> PipelineRunResponse:
    record = _pipelines.get(pipeline_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)

    _runs[run_id] = {
        "run_id": run_id,
        "pipeline_id": pipeline_id,
        "status": "queued",
        "config_overrides": body.config_overrides,
        "trace_sample_rate": body.trace_sample_rate,
        "started_at": now,
    }

    # TODO: Launch background execution when pipeline executor is connected
    # import asyncio
    # asyncio.create_task(_execute_pipeline(run_id, pipeline_id, body))

    logger.info("pipeline_run_triggered", pipeline_id=pipeline_id, run_id=run_id)
    return PipelineRunResponse(
        run_id=run_id,
        pipeline_id=pipeline_id,
        status="queued",
        started_at=now,
    )
```

---

### [HIGH] 9. Eval scores endpoint silently returns all zeros

- **File:** `backend/api/routes/evals.py`
- **Line:** 43–55
- **Issue:** `_compute_scores` iterates metrics but always returns `0.0` for each. A client submitting an eval gets back a result with all-zero scores, `aggregate_score: 0.0`, and `status: "completed"`. There's no indication the eval engine isn't actually running. This produces misleading production data.
- **Suggestion:** Raise `NotImplementedError` or at minimum log a warning and set a `"placeholder": true` flag in the response metadata. Better yet, wire in the real `EvalEngine`.
- **Code:**
```python
# Option A — raise explicitly
def _compute_scores(trajectory: dict[str, Any], metrics: list[str]) -> dict[str, float]:
    raise NotImplementedError(
        "Evaluation engine not yet connected. "
        "Use EvalEngine from backend.eval.engine for real scoring."
    )

# Option B — wire in the real engine
from backend.eval.engine import EvalEngine
from backend.eval.models import Trajectory as EvalTrajectory

def _compute_scores(trajectory: dict[str, Any], metrics: list[str]) -> dict[str, float]:
    eval_traj = EvalTrajectory(**trajectory)
    engine = EvalEngine(metrics=metrics)
    # Note: engine.run() is sync, but route is async
    import asyncio
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, engine.run, eval_traj)
    return result.scores
```

---

### [HIGH] 10. `_should_sample` re-imports `random` on every call

- **File:** `backend/core/tracer.py`
- **Line:** 261–264
- **Issue:** `import random` is inside `_should_sample()`, which is called for every pipeline step. While Python caches imports, the repeated lookup adds unnecessary overhead in a hot path.
- **Suggestion:** Move the import to the module level.
- **Code:**
```python
# BEFORE (line 1-10 area)
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator

import structlog

# ADD:
import random

# THEN in _should_sample:
def _should_sample(self) -> bool:
    return random.random() < self._sample_rate  # no import needed
```

---

### [HIGH] 11. Two incompatible `ParetoPoint` models

- **File:** `backend/optimizer/pareto_optimizer.py:34` and `backend/api/schemas.py:194`
- **Issue:** Both define `ParetoPoint` but with different fields. The optimizer version has `config: PipelineConfig` while the API version has `params: dict[str, Any]`. If code imports from both, the second import shadows the first.
- **Suggestion:** Rename the optimizer's `ParetoPoint` to `OptunaParetoPoint` or `ParetoTrialPoint`, and create a conversion method to the API schema.

---

### [HIGH] 12. `ConnectionManager` is a module-level singleton — not testable

- **File:** `backend/api/websocket.py`
- **Line:** 71
- **Issue:** `manager = ConnectionManager()` is a global singleton. Tests can't inject a fresh instance, and connections from one test can leak into another.
- **Suggestion:** Use FastAPI's dependency injection to provide the manager, or at minimum provide a factory function for testing.

---

### [MEDIUM] 13. Duplicate status enums: `StepStatus` vs `TraceStatus`

- **File:** `backend/core/config.py:41–49` and `backend/api/schemas.py:107–112`
- **Issue:** `StepStatus` and `TraceStatus` have identical values (`pending`, `running`, `completed`/`success`, `failed`). Having two separate enums for the same concept leads to comparison bugs and confusion.
- **Suggestion:** Unify into a single enum. Use `StepStatus` from core everywhere, or create a shared `Status` enum.

---

### [MEDIUM] 14. Duplicate severity enums: `GuardrailSeverity` vs `RiskLevel`

- **File:** `backend/core/config.py:32–38` and `backend/guardrails/filters/base.py:22–26`
- **Issue:** Same concept, different names and locations. `GuardrailSeverity` has LOW/MEDIUM/HIGH/CRITICAL, and `RiskLevel` has the same values. They're used in different contexts but represent the same thing.
- **Suggestion:** Unify under one enum, ideally in `core/config.py`.

---

### [MEDIUM] 15. `ContextRelevanceMetric` filters steps by string comparison

- **File:** `backend/eval/metrics/context_relevance.py`
- **Line:** 84, 92–96
- **Issue:** `_is_relevant` and `aggregate_steps` filter steps using `s.details != "Non-retrieval step — skipped."`. This is extremely fragile — any change to the details string breaks the filter silently.
- **Suggestion:** Add a boolean `skipped` or `relevant` field to `StepScore` instead of relying on string matching.

---

### [MEDIUM] 16. `config_sweeper.py` placeholder pruned count

- **File:** `backend/optimizer/config_sweeper.py`
- **Line:** 335
- **Issue:** `pruned = sum(1 for t in all_trials if False)` always evaluates to 0. The comment says "placeholder; pruner counts via study" but the actual pruned count is never computed.
- **Code:**
```python
# AFTER
pruned = sum(1 for t in study.trials if t.state == optuna.trial.TrialState.PRUNED)
```

---

### [MEDIUM] 17. `ft_trigger.py` potential division by zero in `_build_recommendation`

- **File:** `backend/optimizer/ft_trigger.py`
- **Line:** 435
- **Issue:** `comparison.score_delta / comparison.baseline_avg_score * 100` will raise `ZeroDivisionError` if `baseline_avg_score` is 0.0.
- **Suggestion:** Guard with a zero check.
- **Code:**
```python
# BEFORE
f"({comparison.score_delta / comparison.baseline_avg_score * 100:+.1f}%). "

# AFTER
f"({comparison.score_delta / max(comparison.baseline_avg_score, 1e-9) * 100:+.1f}%). "
```

---

### [MEDIUM] 18. `_run_eval_fn_sync` / `_run_guardrail_eval_sync` / `_run_eval_sync` are duplicated

- **File:** `backend/optimizer/config_sweeper.py:358`, `pareto_optimizer.py:327`, `guardrail_tuner.py:294`
- **Issue:** Three nearly identical async-to-sync bridge functions exist across the optimizer module. Each wraps an async callable with `anyio.from_thread.run`.
- **Suggestion:** Extract into a shared utility:
```python
# backend/optimizer/_utils.py
import anyio
from typing import Any, Callable, Awaitable

def run_async_in_thread(fn: Callable[..., Awaitable[Any]], *args: Any) -> Any:
    async def _inner():
        return await fn(*args)
    return anyio.from_thread.run(_inner)
```

---

### [MEDIUM] 19. Agglomerative clustering is O(n³) — no warning on large inputs

- **File:** `backend/analyzer/failure_clustering.py`
- **Line:** 185–223
- **Issue:** The single-linkage agglomerative clustering implementation has O(n³) complexity (nested loops over active clusters at each merge step). For large failure sets (thousands), this will be extremely slow with no warning to the caller.
- **Suggestion:** Add a warning or fallback to sklearn's clustering for large inputs. Document the complexity limit.

---

### [MEDIUM] 20. `EvalCompareRequest` schema defined but route uses query params

- **File:** `backend/api/schemas.py:92–93` and `backend/api/routes/evals.py:112–116`
- **Issue:** `EvalCompareRequest` is a Pydantic model with `eval_ids: list[str]` field. But the route `GET /evals/compare` accepts query parameters `eval_a` and `eval_b` — the schema is never used for validation.
- **Suggestion:** Either use the schema (change to POST) or remove the unused schema.

---

### [MEDIUM] 21. `_erf` error function approximation has limited accuracy

- **File:** `backend/analyzer/regression_detector.py`
- **Line:** 132–144
- **Issue:** The polynomial approximation of `erf()` (Abramowitz and Stegun 7.1.26) has max error ~1.5e-7. For large |x| > 6, the approximation degrades significantly. Should document the valid input range.
- **Suggestion:** Add a `clamp` on the input or document the limitation.

---

### [MEDIUM] 22. `GuardrailFPRateMetric` filter matching by string `metric_name`

- **File:** `backend/eval/metrics/guardrail_fp_rate.py`
- **Line:** 90
- **Issue:** `guardrail_steps = [s for s in step_scores if s.metric_name == self.name]` filters by string comparison on the metric name. This works but is fragile if metric names are ever renamed.
- **Suggestion:** Use a more robust identifier (e.g., an enum or type check).

---

### [MEDIUM] 23. `toxicity.py` profanity regexes are overly broad

- **File:** `backend/guardrails/filters/toxicity.py`
- **Line:** 38–44
- **Issue:** Pattern `r"\b(a+[\W_]*s+[\W_]*s+)\b"` matches "ass" but also "accessible", "assessment", "pass", "grass", etc. The word boundary anchors help but don't fully prevent false positives on common English words.
- **Suggestion:** Consider using a more targeted regex or adding a blocklist of common false-positive words.

---

### [LOW] 24. Inconsistent `structlog.get_logger()` usage

- **File:** `backend/guardrails/stack.py:18`, `backend/guardrails/filters/base.py:13`, `backend/guardrails/compounding_analyzer.py:17`
- **Issue:** These files call `structlog.get_logger()` without `__name__`, while all other files use `structlog.get_logger(__name__)`. This means log messages from these modules won't have a module identifier, making debugging harder.
- **Suggestion:** Use `structlog.get_logger(__name__)` consistently.

---

### [LOW] 25. Empty `__init__.py` files with no `__all__`

- **File:** `backend/optimizer/__init__.py`, `backend/analyzer/__init__.py`, `backend/db/__init__.py`, `backend/fine_tuning/__init__.py`
- **Issue:** These module `__init__.py` files are empty. The optimizer and analyzer modules have substantial public APIs that should be exported for convenience.
- **Suggestion:** Add `__all__` exports:
```python
# backend/optimizer/__init__.py
from .config_sweeper import ConfigSweeper, SweepResult
from .pareto_optimizer import ParetoOptimizer, ParetoResult
from .guardrail_tuner import GuardrailTuner, TunerResult
from .ft_trigger import FTTrigger, FTTriggerResult

__all__ = ["ConfigSweeper", "ParetoOptimizer", "GuardrailTuner", "FTTrigger", ...]
```

---

### [LOW] 26. Missing type hint for `**config` parameter

- **File:** `backend/eval/metrics/__init__.py`
- **Line:** 31
- **Issue:** `def get_metric(name: str, **config) -> BaseMetric:` — the `**config` dict has no type annotation. With `mypy --strict`, this would be flagged.
- **Code:**
```python
def get_metric(name: str, **config: Any) -> BaseMetric:
```

---

### [LOW] 27. `Step` model `step_id` is `int` — potential confusion with `TrajectoryStep.step_id` which is `str`

- **File:** `backend/eval/models.py:36` vs `backend/core/tracer.py:60`
- **Issue:** `Step.step_id` is `int` (sequential) while `TrajectoryStep.step_id` is a 12-char hex string (UUID). These represent different concepts but share the same field name. When bridging between the two systems, confusion is likely.
- **Suggestion:** Rename `Step.step_id` to `step_index` or `order` to clarify it's a sequence number.

---

### [LOW] 28. `guardrail_tuner.py:64` — `accuracy` property formula is semantically unusual

- **File:** `backend/optimizer/guardrail_tuner.py`
- **Line:** 62–64
- **Issue:** `accuracy` is defined as `(TP + TN) / total_samples`. This is technically correct but only valid when total = TP+TN+FP+FN. The property doesn't verify this invariant.
- **Suggestion:** Add a comment clarifying the assumption, or validate in a `@validator`.

---

### [LOW] 29. No DELETE endpoints for any resources

- **File:** `backend/api/routes/pipelines.py`, `evals.py`, `traces.py`, `optimization.py`
- **Issue:** No DELETE endpoints exist for pipelines, evals, traces, or sweeps. For an in-memory store, this means data grows monotonically. When backed by a database, clients need delete capability for lifecycle management.
- **Suggestion:** Add DELETE endpoints or document the intentional omission.

---

## Architecture & Design Observations

### Strengths
1. **Excellent Pydantic usage** — Config models with proper validation constraints (`Field(ge=..., le=...)`) throughout `core/config.py` and `api/schemas.py`.
2. **Clean Strategy pattern** — `BaseMetric`, `BaseFilter`, `PipelineStep`, `StepScorer` all use proper abstract base classes/protocols.
3. **Good separation of concerns** — The module structure (`core/`, `api/`, `eval/`, `guardrails/`, `optimizer/`, `analyzer/`) is well-organized.
4. **Comprehensive metric suite** — Six well-thought-out metrics with proper scoring, aggregation, and breakdown.
5. **Blame attribution engine** — Sophisticated cascade analysis with counterfactuals and LLM-as-judge rubric is a strong differentiator.
6. **Guardrail compounding analyzer** — The FP overlap analysis across filters is unique and valuable.

### Concerns
1. **No dependency injection** — All components create their own dependencies. The API routes directly manipulate module-level dicts. This makes testing and swapping implementations difficult.
2. **Placeholder implementations everywhere** — Many routes return mock data. While understandable for Phase 1, the placeholder behavior is misleading (all-zero eval scores, non-executing pipeline runs).
3. **No middleware for authentication** — The API has no auth middleware. Any client can create pipelines, trigger runs, and access traces.
4. **Two Trajectory models** — `backend/core/tracer.py:Trajectory` (dataclass) and `backend/eval/models.py:Trajectory` (Pydantic) represent the same concept differently. This is a maintainability concern.

---

## Priority Fix Roadmap

| Priority | Fix | Effort |
|----------|-----|--------|
| P0 | Fix WebSocket double-accept (finding #2) | 5 min |
| P0 | Fix CORS configuration (finding #1) | 30 min |
| P0 | Fix trajectory finalization on exception (finding #3) | 15 min |
| P1 | Fix fail_fast dead code (finding #6) | 5 min |
| P1 | Rename StepScore collision (finding #7) | 20 min |
| P1 | Fix pipeline run endpoint (finding #8) | 1–2 hrs |
| P1 | Wire eval engine or raise error (finding #9) | 1 hr |
| P2 | Unify duplicate enums (#13, #14) | 1 hr |
| P2 | Extract async bridge utility (#18) | 30 min |
| P2 | Add rate limiter eviction (#4) | 30 min |
| P3 | Consistent logger calls (#24) | 10 min |
| P3 | Add `__all__` to empty modules (#25) | 15 min |
| P3 | Type hint cleanup (#26) | 5 min |

---

*Review completed. 45 files, ~5,200 lines reviewed.*
