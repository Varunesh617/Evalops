# Security Audit Report — EvalOps Backend

**Date:** 2026-07-10
**Auditor:** Security Agent (MoE Swarm)
**Scope:** ackend/ directory — all Python modules, pyproject.toml, project structure
**Files Scanned:** 17 Python files, 1 pyproject.toml, full directory tree

---

## Summary

| Severity | Count |
|----------|-------|
| 🔴 Critical | 0 |
| 🟠 High | 2 |
| 🟡 Medium | 6 |
| 🔵 Low | 4 |

---

## Findings

### [HIGH] No .gitignore — Risk of Accidental Secret Commits

- **File:** Project root C:\Users\varun\projects\evalops\
- **Issue:** No .gitignore file exists anywhere in the project. This means .env files, __pycache__/, *.pyc, virtual environments (env/, .venv/), IDE configurations (.idea/, .vscode/), and any future credential files will be committed to version control. This is a critical hygiene gap for a project that will handle API keys and database credentials.
- **Fix:** Create a comprehensive .gitignore at the project root:
  `gitignore
  # Secrets
  .env
  .env.*
  *.pem
  *.key

  # Python
  __pycache__/
  *.pyc
  *.pyo
  .Python
  *.egg-info/
  dist/
  build/

  # Virtual environments
  venv/
  .venv/
  env/

  # IDE
  .idea/
  .vscode/
  *.swp

  # OS
  .DS_Store
  Thumbs.db

  # Test / Coverage
  .coverage
  htmlcov/
  .pytest_cache/
  .mypy_cache/
  `

---

### [HIGH] No Authentication or Authorization Framework

- **File:** ackend/api/ (all routes, when implemented)
- **Issue:** The API layer has no authentication middleware, dependency injection for auth, or authorization checks. The execution plan mentions Supabase-based multi-tenant auth in Phase 5/6, but there is currently zero auth infrastructure. Any routes added to ackend/api/routes/ will be completely open by default. This is particularly critical because the platform will handle PHI/PII data in healthcare/eval contexts.
- **Fix:** Before any API routes are implemented:
  1. Add astapi-security or Supabase Auth middleware
  2. Create an uth.py dependency that validates JWTs/tokens on every route
  3. Add role-based access control (RBAC) middleware
  4. Implement API key validation for service-to-service calls
  5. Default all routes to require authentication (deny by default)

---

### [MEDIUM] Hardcoded Default Database URL

- **File:** ackend/core/config.py
- **Line:** 67
- **Issue:** database_url: SecretStr = SecretStr("postgresql://localhost/evalops") — While the field correctly uses SecretStr (good), the hardcoded default connection string is problematic:
  1. It lacks SSL/TLS enforcement (postgresql+asyncpg:// with sslmode=require)
  2. No authentication credentials in the URL (acceptable for local dev, but could be misleading)
  3. If a developer forgets to override this, the app will connect to an unencrypted local database by default
  `python
  # Current (risky default)
  database_url: SecretStr = SecretStr("postgresql://localhost/evalops")

  # Better (force explicit configuration)
  database_url: SecretStr  # No default — must be set via env var
  `
- **Fix:**
  1. Remove the default value and require DATABASE_URL to be set explicitly via environment variable
  2. If a dev default is needed, use postgresql+asyncpg://localhost/evalops?sslmode=disable with a comment explaining it's non-production
  3. Add a validator that warns when SSL is not enforced in production mode

---

### [MEDIUM] API Keys Optional Without Runtime Validation

- **File:** ackend/core/config.py
- **Lines:** 78, 90, 120
- **Issue:** Three config models define optional API keys that default to None:
  `python
  # Line 78 — RerankerConfig
  api_key: SecretStr | None = None

  # Line 90 — AgentConfig
  api_key: SecretStr | None = None

  # Line 120 — GeneratorConfig
  api_key: SecretStr | None = None
  `
  If a downstream module uses these keys to call external APIs (OpenAI, Cohere, etc.), a None key will cause a runtime error or silent failure. There is no validation that required keys are present when the corresponding service is enabled.
- **Fix:** Add Pydantic model_validator to each config class that checks: if the model/service requires an API key and none is provided (and no env var is set), raise a clear ValidationError at startup rather than failing at runtime.

---

### [MEDIUM] No Security Dependencies in pyproject.toml

- **File:** pyproject.toml
- **Lines:** 32-40
- **Issue:** The dev dependencies do not include any security scanning tools:
  `	oml
  # Current dev deps — no security tools
  dev = [
      "pytest>=8.0.0",
      "pytest-asyncio>=0.24.0",
      "pytest-cov>=5.0.0",
      "ruff>=0.5.0",
      "mypy>=1.10.0",
      "pre-commit>=3.8.0",
  ]
  `
  Missing tools: andit (Python security linter), safety (dependency vulnerability scanner), pip-audit.
- **Fix:** Add security tools to dev dependencies:
  `	oml
  dev = [
      # ... existing ...
      "bandit[toml]>=1.7.0",
      "pip-audit>=2.7.0",
  ]
  `
  And add bandit config to pyproject.toml:
  `	oml
  [tool.bandit]
  exclude_dirs = ["tests"]
  `

---

### [MEDIUM] No Rate Limiting Infrastructure

- **File:** ackend/api/ (missing middleware)
- **Issue:** The project uses FastAPI but has no rate limiting middleware or configuration. The execution plan mentions "rate limiting + cost controls" in Phase 6, but the foundation should be laid earlier. Without rate limiting:
  1. Computationally expensive eval operations can be abused for DoS
  2. LLM API costs can spiral from repeated requests
  3. Guardrail filters can be bypassed via rapid-fire requests
- **Fix:** Add slowapi or astapi-limiter as a dependency and configure rate limiting middleware early:
  `python
  from slowapi import Limiter
  from slowapi.util import get_remote_address

  limiter = Limiter(key_func=get_remote_address)
  app.state.limiter = limiter
  `

---

### [MEDIUM] Dependency Versions Lack Upper Bounds

- **File:** pyproject.toml
- **Lines:** 14-30
- **Issue:** All dependencies use >= without upper bounds:
  `	oml
  "fastapi>=0.115.0",
  "sqlalchemy>=2.0.0",
  "pydantic>=2.0.0",
  `
  This means pip install could pull in a future major version with breaking changes or known vulnerabilities. There is no lock file (equirements.lock or poetry.lock) to pin exact versions.
- **Fix:**
  1. Add upper bounds for critical dependencies: astapi>=0.115.0,<1.0.0
  2. Generate a lock file with pip-compile or uv lock
  3. Add safety or pip-audit to CI to detect known CVEs in pinned versions

---

### [LOW] Exception Messages Could Leak Internal Details

- **File:** ackend/core/tracer.py
- **Lines:** 283-288
- **Issue:** When a pipeline step fails, the full exception message is captured:
  `python
  except Exception as exc:
      step.finish(
          status=StepStatus.FAILED,
          error=str(exc),          # ← full exception message
          error_type=type(exc).__qualname__,
      )
  `
  The str(exc) can contain sensitive internal details (file paths, database connection strings, API key fragments from SDK errors, stack trace info). These are stored in the trajectory and could be exposed via the API or logs.
- **Fix:**
  1. Sanitize error messages before storage: strip file paths, connection strings, and credentials
  2. Add an error message allowlist pattern (only keep known-safe message formats)
  3. Log full errors to a secured audit log, but sanitize what goes into trajectory payloads
  `python
  import re
  def _sanitize_error(msg: str) -> str:
      msg = re.sub(r'postgresql://[^\s]+', 'postgresql://***', msg)
      msg = re.sub(r'password[=:]\s*\S+', 'password=***', msg)
      return msg[:500]  # Truncate long messages
  `

---

### [LOW] Query Text Logged (Potential PII Exposure)

- **File:** ackend/eval/metrics/base.py
- **Line:** 39
- **Issue:** The metric evaluator logs the first 120 characters of the trajectory query:
  `python
  self._log.debug(
      "evaluating_trajectory",
      step_count=len(trajectory.steps),
      query=trajectory.query[:120],  # ← PII risk
  )
  `
  In a healthcare/eval context, queries may contain PHI (patient data, medical terms, personal identifiers). Even truncated, this can leak sensitive data into structured logs.
- **Fix:**
  1. Make query logging configurable via a LOG_QUERIES environment variable (default: False)
  2. When logging is enabled, hash or mask the query: hashlib.sha256(trajectory.query.encode()).hexdigest()[:16]
  3. Ensure structlog is configured with a log redaction processor for production

---

### [LOW] Non-Cryptographic Random Used for Sampling

- **File:** ackend/core/tracer.py
- **Line:** 262-264
- **Issue:** The tracer uses andom.random() for OpenTelemetry sampling decisions:
  `python
  def _should_sample(self) -> bool:
      import random
      return random.random() < self._sample_rate
  `
  While not a security vulnerability for sampling, andom is predictable and could be manipulated if an attacker controls the sample rate. The import random is also inside the method body on every call.
- **Fix:** Move import random to module level. For security-sensitive sampling, consider secrets.randbelow(), but this is low priority since sampling decisions are not security-critical.

---

### [LOW] No Input Sanitization for Guardrail Filter Inputs

- **File:** ackend/guardrails/filters/base.py
- **Line:** 92
- **Issue:** The BaseFilter.check() method accepts raw input_text, context, and output strings without any input validation or sanitization:
  `python
  def check(self, input_text: str, *, context: str = "", output: str = "") -> FilterResult:
  `
  Subclass implementations could be vulnerable to injection attacks if they process these strings unsafely (e.g., building regex patterns, constructing shell commands, or passing to external services).
- **Fix:**
  1. Add input length limits in the base class: reject inputs exceeding a configurable max length (e.g., 100,000 chars)
  2. Document that subclasses MUST NOT use input_text in shell commands or raw SQL
  3. Add a max_input_length parameter to BaseFilter.__init__

---

## Positive Findings ✅

The following security best practices were observed in the codebase:

1. **SecretStr for sensitive values** — database_url, pi_key fields use Pydantic's SecretStr, which prevents accidental logging/serialization of secrets. ✅
2. **Pydantic field validation** — All numeric configs use Field(ge=, le=) constraints to prevent invalid/absurd values. ✅
3. **No hardcoded credentials** — No API keys, passwords, or tokens are hardcoded in the source. ✅
4. **No SQL injection surface** — No raw SQL queries found; the project uses SQLAlchemy ORM. ✅
5. **No command injection surface** — No os.system(), subprocess, eval(), or exec() calls found. ✅
6. **Structlog for structured logging** — Using structlog instead of print() statements. ✅
7. **Guardrail fail-closed default** — ail_open: bool = False means guardrails block by default on error. ✅
8. **Frozen dataclasses** — TokenUsage, FilterResult use rozen=True to prevent mutation. ✅
9. **No sensitive data in log output** — Filter results log only decision/score/duration, not input content. ✅
10. **Timeout enforcement** — All config models include 	imeout_seconds with minimum bounds. ✅

---

## Recommendations Summary

| Priority | Action | Effort |
|----------|--------|--------|
| 🔴 Urgent | Create .gitignore | 10 min |
| 🔴 Urgent | Add auth middleware before any routes | 1-2 days |
| 🟡 Soon | Remove hardcoded DB URL default | 15 min |
| 🟡 Soon | Add API key validation at config load | 30 min |
| 🟡 Soon | Add bandit + pip-audit to dev deps | 15 min |
| 🟡 Soon | Add rate limiting middleware | 2-4 hours |
| 🟡 Soon | Pin dependency upper bounds + lock file | 1 hour |
| 🔵 Later | Sanitize error messages in tracer | 1 hour |
| 🔵 Later | Make query logging opt-in | 30 min |
| 🔵 Later | Add input length limits to filters | 30 min |
