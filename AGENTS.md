# AGENTS.md — EvalOps

Guidance for AI agents and contributors working in this repo. Follow these conventions
whenever making changes.

## Project layout

- `backend/` — Python 3.13 FastAPI service (api, core, eval, optimizer, diagnosis, guardrails, plugins, tuning, db).
- `frontend/` — Next.js 16 + React 19 + TypeScript app (App Router, Tailwind).
- `tests/` — pytest suites: `unit/`, `integration/`, `e2e/`.
- `docker/` — `Dockerfile.backend`, `docker-compose.yml`.
- `frontend/Dockerfile` — Next.js standalone image.
- `Jenkinsfile` — CI/CD pipeline (see Deploy).

## Frontend rules

- ALWAYS use the typed API client in `frontend/src/lib/api.ts`. NEVER call `fetch` directly
  from pages/components. Add new endpoints as typed methods + interfaces there.
- Keep `RootLayout` (`src/app/layout.tsx`) a **server component** that exports `metadata`.
  Interactive shell logic lives in the client component `src/components/AppShell.tsx`.
- Client-only code needs `"use client"`. Do not use hooks or event handlers in server components.
- Errors/toasts go through `src/lib/error-context.tsx` (`useError().showToast`). It is provided
  in `layout.tsx` via `ErrorProvider`; `useError` only works inside it.
- Loading states use `src/components/LoadingSkeleton.tsx`.
- Nav links are registered in `src/components/Sidebar.tsx`.
- Match existing Tailwind/zinc styling and dark-mode classes already used across pages.

## Backend rules

- Python 3.13, FastAPI, Pydantic v2, SQLAlchemy async. Structured logging via `structlog`.
- Config/enums live in `backend/core/config.py`; DB access via `backend/db/repositories.py`.
- Validate input at system boundaries. Never log or commit secrets.

## Before committing (required verification)

Run and ensure all pass:

- Frontend: `cd frontend && npx tsc --noEmit && npm run build`
  - `npm run lint` may report strict React 19 / Next 16 rule violations
    (`react-hooks/set-state-in-effect`, `react-hooks/purity`) that also exist in older
    code and do not block `next build`. Do not regress beyond existing patterns.
- Backend: `python -m pytest -q` (target: all tests pass; currently 359).

Watch for files that contain literal `\n` instead of real newlines — several frontend files
were corrupted this way and never compiled. When rewriting a file, verify with `tsc`.

## Commit & Git conventions

- Conventional-commit style prefixes: `feat(scope):`, `fix:`, `ci:`, etc. Use a short subject
  plus `-m` bullet lines for detail (see `git log`).
- Do NOT commit session/verification artifacts (e.g. `FIX_VERIFICATION.md`,
  `PHASE_A_VERIFICATION.md`). Exclude them when staging.
- Only commit/push when explicitly asked.
- Remote: `github.com/Varunesh617/Evalops` (default branch pushed: `master`).
- When pushing with a token, use an ephemeral `http.extraheader` so the token is never written
  to `.git/config`; never hardcode tokens in the repo.

## Deploy (CI/CD via Jenkins)

- Pipeline is defined in `Jenkinsfile` (declarative, `agent any`).
- Jenkins node must have: Python 3.13, Node.js 20, Docker + Docker Compose v2, git, curl.
- Flow per build: preflight -> backend venv install + `npm ci` -> parallel lint/type-check
  (ruff, mypy, eslint, tsc) -> pytest (unit+integration) -> `next build` -> bandit -> build
  both Docker images.
- On `main`/`master` only: deploy via `docker compose -f docker/docker-compose.yml up -d --build`
  followed by a backend `/health` smoke test.
- Lint/type/security stages are non-blocking (mark build UNSTABLE); tests and builds are hard gates.
- Regular deployments: push to `main`/`master` (webhook or SCM poll triggers the job).

## Local run

- Full stack: `docker compose -f docker/docker-compose.yml up -d --build`
  (backend :8000, frontend :3000, postgres :5432, redis :6379).
- Backend API base URL for the frontend: `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).
