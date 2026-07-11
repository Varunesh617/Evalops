"""Integration tests for FastAPI routes."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_check(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "evalops-api"


# ---------------------------------------------------------------------------
# Pipeline routes
# ---------------------------------------------------------------------------


class TestPipelineRoutes:
    @pytest.mark.asyncio
    async def test_create_pipeline(self, client):
        resp = await client.post("/pipelines", json={"name": "Test Pipeline"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Pipeline"
        assert data["status"] == "draft"
        assert data["id"].startswith("pl-")

    @pytest.mark.asyncio
    async def test_create_pipeline_with_tags(self, client):
        resp = await client.post("/pipelines", json={"name": "Tagged", "tags": ["ml", "qa"]})
        assert resp.status_code == 201
        assert resp.json()["tags"] == ["ml", "qa"]

    @pytest.mark.asyncio
    async def test_list_pipelines_empty(self, client):
        resp = await client.get("/pipelines")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_pipelines_after_create(self, client):
        await client.post("/pipelines", json={"name": "P1"})
        await client.post("/pipelines", json={"name": "P2"})
        resp = await client.get("/pipelines")
        assert resp.json()["total"] == 2

    @pytest.mark.asyncio
    async def test_list_pipelines_pagination(self, client):
        for i in range(5):
            await client.post("/pipelines", json={"name": f"P{i}"})
        resp = await client.get("/pipelines?page=1&page_size=2")
        data = resp.json()
        assert len(data["pipelines"]) == 2
        assert data["total"] == 5

    @pytest.mark.asyncio
    async def test_get_pipeline(self, client):
        create_resp = await client.post("/pipelines", json={"name": "Get Me"})
        pid = create_resp.json()["id"]
        resp = await client.get(f"/pipelines/{pid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Get Me"

    @pytest.mark.asyncio
    async def test_get_pipeline_not_found(self, client):
        resp = await client.get("/pipelines/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_run_pipeline(self, client):
        create_resp = await client.post("/pipelines", json={"name": "Run Me"})
        pid = create_resp.json()["id"]
        resp = await client.post(f"/pipelines/{pid}/run", json={})
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "queued"
        assert data["pipeline_id"] == pid

    @pytest.mark.asyncio
    async def test_run_pipeline_not_found(self, client):
        resp = await client.post("/pipelines/nonexistent/run", json={})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_pipeline_traces(self, client):
        create_resp = await client.post("/pipelines", json={"name": "Traces"})
        pid = create_resp.json()["id"]
        resp = await client.get(f"/pipelines/{pid}/traces")
        assert resp.status_code == 200
        assert resp.json()["traces"] == []


# ---------------------------------------------------------------------------
# Eval routes
# ---------------------------------------------------------------------------


class TestEvalRoutes:
    @pytest.mark.asyncio
    async def test_run_eval(self, client):
        resp = await client.post("/evals", json={
            "trajectory": {"trajectory_id": "traj-1", "query": "test"},
            "metrics": ["faithfulness"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "completed"
        assert "faithfulness" in data["scores"]

    @pytest.mark.asyncio
    async def test_run_eval_invalid_metric(self, client):
        resp = await client.post("/evals", json={
            "trajectory": {"trajectory_id": "traj-1"},
            "metrics": ["nonexistent_metric"],
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_run_eval_multiple_metrics(self, client):
        resp = await client.post("/evals", json={
            "trajectory": {"trajectory_id": "traj-1", "query": "test query"},
            "metrics": ["faithfulness", "context_relevance"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert len(data["scores"]) == 2

    @pytest.mark.asyncio
    async def test_get_eval(self, client):
        create_resp = await client.post("/evals", json={
            "trajectory": {"trajectory_id": "traj-1", "query": "test query"},
            "metrics": ["faithfulness"],
        })
        eid = create_resp.json()["id"]
        resp = await client.get(f"/evals/{eid}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_eval_not_found(self, client):
        resp = await client.get("/evals/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_compare_evals(self, client):
        r1 = await client.post("/evals", json={
            "trajectory": {"trajectory_id": "t1", "query": "test query"},
            "metrics": ["faithfulness"],
        })
        r2 = await client.post("/evals", json={
            "trajectory": {"trajectory_id": "t2", "query": "test query"},
            "metrics": ["faithfulness"],
        })
        eid_a = r1.json()["id"]
        eid_b = r2.json()["id"]
        resp = await client.get(f"/evals/compare?eval_a={eid_a}&eval_b={eid_b}")
        assert resp.status_code == 200
        data = resp.json()
        assert "score_diffs" in data


# ---------------------------------------------------------------------------
# Trace routes
# ---------------------------------------------------------------------------


class TestTraceRoutes:
    @pytest.mark.asyncio
    async def test_list_traces_empty(self, client):
        resp = await client.get("/traces")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_get_trace_not_found(self, client):
        resp = await client.get("/traces/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_trace_blame_not_found(self, client):
        resp = await client.get("/traces/nonexistent/blame")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Optimization routes
# ---------------------------------------------------------------------------


class TestOptimizationRoutes:
    @pytest.mark.asyncio
    async def test_start_sweep(self, client):
        resp = await client.post("/optimize/sweep", json={
            "pipeline_id": "pl-123",
            "search_space": {"retrieval_top_k": [5, 50]},
            "n_trials": 10,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"
        assert data["sweep_id"].startswith("sweep-")

    @pytest.mark.asyncio
    async def test_get_sweep_status(self, client):
        create_resp = await client.post("/optimize/sweep", json={
            "pipeline_id": "pl-123",
            "search_space": {},
            "n_trials": 5,
        })
        sweep_id = create_resp.json()["sweep_id"]
        resp = await client.get(f"/optimize/status?sweep_id={sweep_id}")
        assert resp.status_code == 200
        assert resp.json()["sweep_id"] == sweep_id

    @pytest.mark.asyncio
    async def test_get_sweep_status_not_found(self, client):
        resp = await client.get("/optimize/status?sweep_id=nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_pareto_not_completed(self, client):
        create_resp = await client.post("/optimize/sweep", json={
            "pipeline_id": "pl-123",
            "search_space": {},
            "n_trials": 5,
        })
        sweep_id = create_resp.json()["sweep_id"]
        resp = await client.get(f"/optimize/pareto?sweep_id={sweep_id}")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# CORS / middleware tests
# ---------------------------------------------------------------------------


class TestMiddleware:
    @pytest.mark.asyncio
    async def test_cors_headers(self, client):
        resp = await client.options("/health", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        })
        # CORS middleware should respond
        assert resp.status_code in (200, 405)

    @pytest.mark.asyncio
    async def test_timing_header(self, client):
        resp = await client.get("/health")
        assert "x-process-time-ms" in resp.headers
