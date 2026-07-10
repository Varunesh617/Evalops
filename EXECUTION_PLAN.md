# EvalOps — Phase-Wise Execution Plan

## Overview

Multi-agent swarm implementation of EvalOps using MoE (Mixture of Experts) ideology.
Each phase has specialized agents working in parallel with security audits at every stage.

---

## Phase 1: Core Pipeline + Tracer + Blame Attribution
**Duration:** 2 weeks | **Agents:** Coder (core), Security, Reviewer

### Deliverables
- `backend/core/pipeline.py` — Pipeline executor with step tracing
- `backend/core/tracer.py` — OpenTelemetry-compatible trajectory capture
- `backend/core/config.py` — Pydantic config models
- `backend/eval/blame_attribution.py` — Failure root cause analysis
- `backend/eval/trajectory_scorer.py` — Step-by-step scoring

### Agent Tasks
| Agent | Task | Output |
|-------|------|--------|
| `coder-core` | Pipeline executor + tracer | `pipeline.py`, `tracer.py` |
| `coder-blame` | Blame attribution engine | `blame_attribution.py` |
| `security` | Audit data handling, no PHI leaks | Security report |
| `reviewer` | Code quality + design patterns | Review feedback |

---

## Phase 2: Eval Engine + Metrics
**Duration:** 1 week | **Agents:** Coder (eval), Tester

### Deliverables
- `backend/eval/engine.py` — Pluggable eval dispatcher
- `backend/eval/metrics/faithfulness.py`
- `backend/eval/metrics/context_relevance.py`
- `backend/eval/metrics/trajectory_coherence.py`
- `backend/eval/metrics/tool_call_accuracy.py`
- `backend/eval/metrics/guardrail_fp_rate.py`
- `backend/eval/metrics/cost_efficiency.py`

### Agent Tasks
| Agent | Task | Output |
|-------|------|--------|
| `coder-eval` | Eval engine + 6 metrics | `engine.py`, metrics |
| `tester` | Unit tests for each metric | `tests/unit/test_metrics.py` |
| `security` | Validate metric inputs | Security report |

---

## Phase 3: Guardrail Stack + Compounding Analyzer
**Duration:** 1 week | **Agents:** Coder (guardrails), Security

### Deliverables
- `backend/guardrails/stack.py` — Composable guardrail orchestrator
- `backend/guardrails/filters/prompt_injection.py`
- `backend/guardrails/filters/pii.py`
- `backend/guardrails/filters/toxicity.py`
- `backend/guardrails/filters/faithfulness_check.py`
- `backend/guardrails/filters/citation_validator.py`
- `backend/guardrails/compounding_analyzer.py`

### Agent Tasks
| Agent | Task | Output |
|-------|------|--------|
| `coder-guard` | Guardrail stack + filters | Guardrail modules |
| `security` | PII handling, injection prevention | Security report |
| `tester` | Filter accuracy + FP tests | `tests/unit/test_guardrails.py` |

---

## Phase 4: Optimizer + Pareto Frontier
**Duration:** 1 week | **Agents:** Coder (optimizer), Reviewer

### Deliverables
- `backend/optimizer/config_sweeper.py` — Optuna integration
- `backend/optimizer/pareto_optimizer.py` — Cost/quality frontier
- `backend/optimizer/guardrail_tuner.py` — FP minimization
- `backend/analyzer/regression_detector.py`
- `backend/analyzer/failure_clustering.py`

### Agent Tasks
| Agent | Task | Output |
|-------|------|--------|
| `coder-opt` | Optimizer + Pareto search | Optimizer modules |
| `reviewer` | Algorithm correctness review | Review feedback |
| `security` | Resource limits, DoS prevention | Security report |

---

## Phase 5: API + Frontend + Jenkins
**Duration:** 1 week | **Agents:** Coder (api), Coder (frontend), DevOps

### Deliverables
- `backend/api/routes/pipelines.py`
- `backend/api/routes/evals.py`
- `backend/api/routes/traces.py`
- `backend/api/routes/optimization.py`
- `backend/api/websocket.py`
- `frontend/app/dashboard/` — Next.js dashboard
- `Jenkinsfile` — CI/CD pipeline
- `docker/` — Containerization

### Agent Tasks
| Agent | Task | Output |
|-------|------|--------|
| `coder-api` | FastAPI routes + websocket | API layer |
| `coder-fe` | Next.js dashboard | Frontend |
| `devops` | Jenkins + Docker setup | CI/CD config |
| `security` | Auth, rate limiting, HIPAA | Security report |
| `tester` | Integration + e2e tests | Test suite |

---

## Phase 6: Production Hardening
**Duration:** 1 week | **Agents:** All

### Deliverables
- Multi-tenant auth (Supabase)
- Alerting (Slack/PagerDuty)
- Rate limiting + cost controls
- Read-only replay for debugging
- Final security audit
- Documentation

---

## MoE (Mixture of Experts) Pattern

```
                    ┌─────────────┐
                    │   PLANNER   │
                    │  (Lead)     │
                    └──────┬──────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│  CODER-CORE   │  │  CODER-EVAL   │  │  CODER-GUARD  │
│  Pipeline     │  │  Metrics      │  │  Guardrails   │
│  Tracer       │  │  Engine       │  │  Filters      │
│  Blame        │  │  Scorer       │  │  Compounding  │
└───────────────┘  └───────────────┘  └───────────────┘
        │                  │                  │
        ▼                  ▼                  ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│  CODER-OPT    │  │  CODER-API    │  │  CODER-FE     │
│  Optimizer    │  │  FastAPI      │  │  Next.js      │
│  Pareto       │  │  Routes       │  │  Dashboard    │
│  Sweeper      │  │  WebSocket    │  │  Components   │
└───────────────┘  └───────────────┘  └───────────────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ SECURITY │ │  TESTER  │ │ REVIEWER │
        │  Audit   │ │  Tests   │ │  Review  │
        └──────────┘ └──────────┘ └──────────┘
```

---

## Git Commit Strategy

| Phase | Branch | Commits |
|-------|--------|---------|
| 1 | `feature/phase1-core` | `feat(core): pipeline executor`, `feat(core): tracer`, `feat(core): blame attribution` |
| 2 | `feature/phase2-eval` | `feat(eval): engine`, `feat(eval): metrics` |
| 3 | `feature/phase3-guardrails` | `feat(guard): stack`, `feat(guard): filters` |
| 4 | `feature/phase4-optimizer` | `feat(opt): pareto`, `feat(opt): sweeper` |
| 5 | `feature/phase5-api` | `feat(api): routes`, `feat(ci): jenkins` |
| 6 | `main` | Merge all phases |

---

## Security Audit Checklist (Every Phase)

- [ ] No hardcoded secrets/credentials
- [ ] Input validation at boundaries
- [ ] SQL injection prevention
- [ ] Rate limiting on endpoints
- [ ] PHI/PII data handling compliance
- [ ] Dependency vulnerability scan
- [ ] Container image scan
- [ ] Auth token validation
- [ ] Audit logging enabled
- [ ] Error messages don't leak internals
