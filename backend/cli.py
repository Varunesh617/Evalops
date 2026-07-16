"""EvalOps native CLI entrypoint.

Thin Typer wrapper around the backend so the whole platform can later be
bundled into a single native executable (PyInstaller). The CLI object is named
``app`` so the repo-root console script ``evalops = "backend.cli:app"`` resolves.

Subcommands:
    serve   Run the FastAPI service via uvicorn (programmatic).
    run     Execute a pipeline synchronously and print its trajectory summary.
    health  Probe the /health endpoint of a running service.
    version Print the installed package version.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
import typer

from backend.core.config import PipelineConfig

logger = structlog.get_logger(__name__)

app = typer.Typer(
    name="evalops",
    help="EvalOps native CLI — serve, run, and inspect the platform.",
    no_args_is_help=True,
    add_completion=False,
)


def _get_version() -> str:
    """Return the installed evalops package version without importing FastAPI."""
    import importlib.metadata

    try:
        return importlib.metadata.version("evalops")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _build_llm_client(
    provider: str | None,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
) -> Any:
    """Build an LLMClient from provider-name or direct connection flags.

    Preference: explicit --provider (resolved via the registry) else explicit
    connection flags. When nothing is given the client falls back to env / the
    disabled state, so offline runs stay robust.
    """
    from backend.core.llm_client import LLMClient

    if provider:
        try:
            return LLMClient.from_provider(provider, model=model)
        except ValueError as exc:
            logger.warning("llm_provider_unresolved", error=str(exc))
            return LLMClient(model=model, api_key=None)

    if base_url or api_key or model:
        return LLMClient(
            model=model,
            base_url=base_url,
            api_key=api_key,
            provider="auto",
        )

    # No flags: rely on environment defaults; may be unconfigured (offline-safe).
    return LLMClient(model=model, api_key=api_key)


def _print_trajectory(trajectory: Any) -> None:
    """Render a compact trajectory summary to stdout."""
    typer.echo(f"Pipeline : {trajectory.pipeline_id}")
    typer.echo(f"Run ID   : {trajectory.run_id}")
    typer.echo(f"Status   : {'success' if trajectory.succeeded else 'failed'}")
    typer.echo(f"Latency  : {trajectory.latency_ms:.1f} ms")
    typer.echo(f"Tokens   : {trajectory.total_tokens.total_tokens} total "
               f"({trajectory.total_tokens.prompt_tokens} prompt / "
               f"{trajectory.total_tokens.completion_tokens} completion)")
    typer.echo("Steps:")
    for step in trajectory.steps:
        llm_used = step.payload.get("result", {}).get("llm_used")
        llm_tag = f" llm_used={llm_used}" if llm_used is not None else ""
        typer.echo(f"  - {step.step_name:<10} {str(step.status):<8} "
                   f"{step.latency_ms:.1f} ms{llm_tag}")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host."),
    port: int = typer.Option(8000, "--port", help="Bind port."),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev)."),
) -> None:
    """Run the EvalOps API via uvicorn (programmatic)."""
    import uvicorn

    logger.info("cli_serve", host=host, port=port, reload=reload)
    uvicorn.run(
        "backend.api.app:app",
        host=host,
        port=port,
        reload=reload,
        factory=False,
    )


@app.command()
def run(
    pipeline_id: str = typer.Option("default", "--pipeline-id", help="Pipeline id."),
    query: str = typer.Option(..., "--query", help="Query to execute."),
    provider: str | None = typer.Option(None, "--provider", help="Registered provider name."),
    model: str | None = typer.Option(None, "--model", help="Model name override."),
    base_url: str | None = typer.Option(None, "--base-url", help="LLM base URL."),
    api_key: str | None = typer.Option(None, "--api-key", help="LLM API key."),
    config_json: str = typer.Option("{}", "--config-json", help="JSON overrides."),
) -> None:
    """Execute a pipeline locally and print its trajectory summary."""

    overrides: dict[str, Any] = {}
    if config_json.strip():
        try:
            overrides = json.loads(config_json)
        except json.JSONDecodeError as exc:
            logger.error("invalid_config_json", error=str(exc))
            raise typer.BadParameter(f"--config-json is not valid JSON: {exc}") from exc

    overrides.setdefault("pipeline_id", pipeline_id)
    if model is not None:
        overrides.setdefault("agent", {}).update({"model": model})
        overrides.setdefault("generator", {}).update({"model": model})
    if base_url is not None:
        overrides.setdefault("agent", {}).update({"base_url": base_url})
        overrides.setdefault("generator", {}).update({"base_url": base_url})
    if api_key is not None:
        overrides.setdefault("agent", {}).update({"api_key": api_key})
        overrides.setdefault("generator", {}).update({"api_key": api_key})

    logger.info("cli_run", pipeline_id=pipeline_id, query_len=len(query))

    _build_llm_client(provider, model, base_url, api_key)

    config = PipelineConfig(**overrides)
    from backend.core.pipeline import PipelineExecutor

    async def _run() -> Any:
        executor = PipelineExecutor(config=config)
        return await executor.execute(query)

    trajectory = asyncio.run(_run())
    _print_trajectory(trajectory)
    if not trajectory.succeeded:
        raise typer.Exit(code=1)


@app.command()
def health(
    url: str = typer.Option("http://localhost:8000", "--url", help="Service base URL."),
) -> None:
    """Probe the service /health endpoint and print the status."""
    import httpx

    health_url = url.rstrip("/") + "/health"
    logger.info("cli_health", url=health_url)
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(health_url)
    except httpx.HTTPError as exc:
        logger.error("health_check_failed", error=str(exc))
        typer.echo(f"health: UNREACHABLE ({exc})")
        unreachable = True
    else:
        unreachable = False

    if unreachable:
        raise typer.Exit(code=1)

    if resp.is_success:
        typer.echo(f"health: OK {resp.status_code} {resp.text}")
    else:
        typer.echo(f"health: DEGRADED {resp.status_code} {resp.text}")
        raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the installed EvalOps version."""
    typer.echo(_get_version())


if __name__ == "__main__":
    app()
