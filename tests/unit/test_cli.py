"""Tests for the EvalOps native CLI (backend.cli)."""

from __future__ import annotations

from unittest import mock

import httpx
from typer.testing import CliRunner

from backend.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() != ""


def test_health_ok() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "healthy", "service": "evalops-api"}
        )

    with mock.patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.side_effect = _handler
        result = runner.invoke(app, ["health", "--url", "http://localhost:8000"])
    assert result.exit_code == 0
    assert "health: OK" in result.stdout


def test_health_unreachable() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with mock.patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.side_effect = _handler
        result = runner.invoke(app, ["health", "--url", "http://localhost:9999"])
    assert result.exit_code == 1
    assert "UNREACHABLE" in result.stdout


def test_serve_builds_uvicorn_config_without_running() -> None:
    captured: dict[str, object] = {}

    def _fake_run(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    with mock.patch("uvicorn.run", _fake_run):
        result = runner.invoke(
            app, ["serve", "--host", "127.0.0.1", "--port", "9000"]
        )
    assert result.exit_code == 0
    assert captured["args"] == ("backend.api.app:app",)
    kwargs = captured["kwargs"]
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 9000


def test_run_invalid_config_json() -> None:
    result = runner.invoke(
        app,
        ["run", "--pipeline-id", "p1", "--query", "hi", "--config-json", "{bad"],
    )
    assert result.exit_code != 0


def test_run_execute_offline() -> None:
    """Smoke test that `run` executes the skeleton pipeline without an LLM."""
    result = runner.invoke(
        app, ["run", "--pipeline-id", "demo", "--query", "What is 2+2?"]
    )
    assert result.exit_code == 0
    assert "Pipeline : demo" in result.stdout
    assert "Steps:" in result.stdout
    assert "retrieve" in result.stdout
