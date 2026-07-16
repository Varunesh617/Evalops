"""Tests for the unified LLM client and LLM-wired pipeline steps."""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest

from backend.core.config import AgentConfig, GeneratorConfig, PipelineConfig
from backend.core.llm_client import LLMClient, LLMClientError
from backend.core.pipeline import GenerateStep, ReasonStep
from backend.core.tracer import Trajectory


def _fake_openai_payload(text: str, tokens: dict[str, int]) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": text, "role": "assistant"}}],
        "usage": tokens,
    }


def _make_client(
    transport: httpx.MockTransport, **kwargs: Any
) -> tuple[LLMClient, list[dict[str, Any]]]:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "json": request.read().decode() if request.content else "",
            }
        )
        body = _fake_openai_payload("answer", {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        })
        return httpx.Response(200, json=body)

    kwargs.setdefault("base_url", "https://api.openai.com/v1")
    kwargs.setdefault("api_key", "sk-test")
    kwargs["transport"] = transport or httpx.MockTransport(handler)
    client = LLMClient(**kwargs)
    return client, captured


@pytest.mark.asyncio
async def test_complete_openai_success():
    client, captured = _make_client(None)
    result = await client.complete([{"role": "user", "content": "hi"}])
    assert result["text"] == "answer"
    assert result["tokens"]["total_tokens"] == 15
    assert "/chat/completions" in captured[0]["url"]
    assert captured[0]["headers"]["authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_complete_missing_key_raises():
    client = LLMClient(api_key=None, base_url="https://api.openai.com/v1")
    assert not client.configured
    with pytest.raises(LLMClientError):
        await client.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_provider_anthropic_translates():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = {
            "content": [{"type": "text", "text": "claude-answer"}],
            "usage": {"input_tokens": 7, "output_tokens": 3},
        }
        return httpx.Response(200, json=body)

    client = LLMClient(
        model="claude-3-5-sonnet",
        api_key="sk-ant-test",
        base_url="https://api.anthropic.com/v1",
        provider="anthropic",
        transport=httpx.MockTransport(handler),
    )
    result = await client.complete(
        [{"role": "user", "content": "hi"}],
        system="be brief",
    )
    assert result["text"] == "claude-answer"
    assert result["tokens"]["completion_tokens"] == 3
    req = captured[0]
    assert "/messages" in str(req.url)
    assert req.headers["x-api-key"] == "sk-ant-test"
    sent = req.read().decode()
    assert "be brief" in sent


@pytest.mark.asyncio
async def test_provider_auto_detect_from_url():
    client = LLMClient(api_key="x", base_url="https://api.anthropic.com/v1")
    assert client.provider == "anthropic"


@pytest.mark.asyncio
async def test_http_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = LLMClient(
        api_key="sk", base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(LLMClientError):
        await client.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_reason_step_fallback_without_key():
    step = ReasonStep()
    ctx = __import__("backend.core.pipeline", fromlist=["PipelineContext"]).PipelineContext(
        config=PipelineConfig(), trajectory=Trajectory(), query="q"
    )
    ctx.results["rerank"] = {"documents": [{"content": "doc1"}]}
    result = await step.execute(ctx)
    assert result["status"] == "success"
    assert result["llm_used"] is False


@pytest.mark.asyncio
async def test_reason_step_calls_llm(monkeypatch):
    from backend.core import pipeline as pipeline_mod

    captured: dict[str, Any] = {}

    class FakeClient:
        configured = True

        def __init__(self, *args, **kwargs):
            captured["init"] = kwargs

        async def complete(self, messages, **kw):
            captured["messages"] = messages
            return {
                "text": "my reasoning",
                "tokens": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "raw": {},
            }

    monkeypatch.setattr(pipeline_mod, "LLMClient", FakeClient)
    step = ReasonStep()
    cfg = PipelineConfig(agent=AgentConfig(api_key="sk", model="gpt-4o"))
    ctx = pipeline_mod.PipelineContext(
        config=cfg, trajectory=Trajectory(), query="what is 2+2?"
    )
    ctx.results["rerank"] = {"documents": [{"content": "two plus two is four"}]}
    result = await step.execute(ctx)
    assert result["reasoning"] == "my reasoning"
    assert result["llm_used"] is True
    assert "what is 2+2" in captured["messages"][0]["content"]


@pytest.mark.asyncio
async def test_generate_step_calls_llm_and_cites(monkeypatch):
    from backend.core import pipeline as pipeline_mod

    class FakeClient:
        configured = True

        def __init__(self, *args, **kwargs):
            pass

        async def complete(self, messages, **kw):
            return {
                "text": "The answer is four.",
                "tokens": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "raw": {},
            }

    monkeypatch.setattr(pipeline_mod, "LLMClient", FakeClient)
    step = GenerateStep()
    cfg = PipelineConfig(generator=GeneratorConfig(api_key="sk", include_citations=True))
    ctx = pipeline_mod.PipelineContext(config=cfg, trajectory=Trajectory(), query="q")
    ctx.results["rerank"] = {"documents": [{"content": "doc1"}, {"content": "doc2"}]}
    ctx.results["reason"] = {"reasoning": "think hard"}
    result = await step.execute(ctx)
    assert "The answer is four." in result["text"]
    assert "Sources:" in result["text"]
    assert result["llm_used"] is True
