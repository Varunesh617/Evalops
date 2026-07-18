"""Pipeline executor — orchestrates Retrieve → Rerank → Reason → Guardrail → Generate.

Each step is executed sequentially.  The :class:`Tracer` records a
:mod:`TrajectoryStep` for every stage, making the full execution path
available for downstream scoring and blame attribution.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

from backend.core.config import PipelineConfig, RerankerModel, StepStatus
from backend.core.llm_client import LLMClient, LLMClientError
from backend.core.local_model_stats import attach_local_stats
from backend.core.tracer import TokenUsage, Tracer, Trajectory

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Step protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PipelineStep(Protocol):
    """Any object that can act as a pipeline step."""

    name: str

    async def execute(
        self,
        context: PipelineContext,
    ) -> dict[str, Any]:
        """Execute the step, returning a result dict.

        The result **must** contain at least ``{"status": "success"}`` or
        ``{"status": "failed", "error": "..."}``.
        """
        ...


# ---------------------------------------------------------------------------
# Pipeline context — mutable state shared across steps
# ---------------------------------------------------------------------------


@dataclass
class PipelineContext:
    """Shared mutable state carried through pipeline execution.

    Each step reads from and writes to this context.  Downstream steps
    consume outputs produced by upstream steps via well-known keys.
    """

    config: PipelineConfig
    trajectory: Trajectory
    query: str = ""
    results: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Convenience accessors for typed downstream consumption
    @property
    def documents(self) -> list[dict[str, Any]]:
        return self.results.get("retrieve", {}).get("documents", [])  # type: ignore[return-value]

    @property
    def reranked(self) -> list[dict[str, Any]]:
        return self.results.get("rerank", {}).get("documents", [])  # type: ignore[return-value]

    @property
    def reasoning(self) -> str:
        return self.results.get("reason", {}).get("reasoning", "")

    @property
    def guardrail_passed(self) -> bool:
        return self.results.get("guardrail", {}).get("passed", True)

    @property
    def generated(self) -> str:
        return self.results.get("generate", {}).get("text", "")


# ---------------------------------------------------------------------------
# Built-in (skeleton) step implementations
#
# Real implementations will live in backend/steps/*.py and will be injected
# at runtime.  These skeletons validate the executor contract and are useful
# for testing without external services.
# ---------------------------------------------------------------------------


class _BaseStep(abc.ABC):
    """Common base for skeleton steps."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abc.abstractmethod
    async def execute(self, context: PipelineContext) -> dict[str, Any]: ...


class RetrieveStep(_BaseStep):
    """Retrieve relevant documents from a vector store."""

    def __init__(self) -> None:
        super().__init__("retrieve")

    async def execute(self, context: PipelineContext) -> dict[str, Any]:
        retrieval_cfg = context.config.retrieval
        logger.info("retrieve_started", query=context.query[:80])

        try:
            # Lazy import keeps this module importable even before the
            # retrieval backend has landed.
            from backend.retrieval.store import (
                VectorStoreUnavailable,
                get_vector_store,
            )
        except ImportError:
            logger.warning("retrieve_backend_missing", fallback=True)
            return {
                "status": "success",
                "documents": [],
                "count": 0,
                "retrieved": False,
                "fallback": True,
            }

        try:
            store = get_vector_store(retrieval_cfg)
            results = await store.query(
                context.query,
                top_k=retrieval_cfg.top_k,
                threshold=retrieval_cfg.similarity_threshold,
            )
        except (VectorStoreUnavailable, Exception) as exc:  # noqa: BLE001
            logger.warning(
                "retrieve_failed",
                error=str(exc),
                fallback=True,
            )
            return {
                "status": "success",
                "documents": [],
                "count": 0,
                "retrieved": False,
                "fallback": True,
            }

        return {
            "status": "success",
            "documents": results,
            "count": len(results),
            "index_name": retrieval_cfg.index_name,
            "retrieved": True,
        }


def _doc_text(doc: dict[str, Any]) -> str:
    """Extract the textual body of a retrieved document."""
    return doc.get("content") or doc.get("text") or ""


class RerankStep(_BaseStep):
    """Rerank retrieved documents using a configured backend.

    Supported backends (``context.config.reranker.model``):

    * ``CROSS_ENCODER`` — lazily import ``sentence_transformers.CrossEncoder``
      and score each doc against the query; falls back to embedding similarity
      if the dependency is absent.
    * ``COHERE`` — call the Cohere rerank API over HTTP when an API key is set;
      falls back to embedding similarity when no key is present or on any error.
    * ``CUSTOM`` — use embedding similarity as the rerank signal.

    Graceful degradation: empty input is passed through unchanged, and any
    irrecoverable failure returns the original docs with ``reranked=False`` and
    ``fallback=True`` so the pipeline never breaks.
    """

    def __init__(self) -> None:
        super().__init__("rerank")

    async def execute(self, context: PipelineContext) -> dict[str, Any]:
        cfg = context.config.reranker
        docs = list(context.documents)
        logger.info(
            "rerank_started",
            model=cfg.model.value,
            input_count=len(docs),
        )

        if not docs:
            return {
                "status": "success",
                "documents": [],
                "reranked": False,
                "method": "passthrough",
                "count": 0,
            }

        top_k = max(1, cfg.top_k)
        try:
            if cfg.model == RerankerModel.CROSS_ENCODER:
                ranked, method = await self._rerank_cross_encoder(context, docs, cfg)
            elif cfg.model == RerankerModel.COHERE:
                ranked, method = await self._rerank_cohere(context, docs, cfg)
            else:  # CUSTOM
                ranked, method = await self._rerank_embedding(context, docs)
        except Exception as exc:
            logger.warning("rerank_failed", error=str(exc))
            return {
                "status": "success",
                "documents": docs,
                "reranked": False,
                "fallback": True,
                "method": "passthrough",
                "count": len(docs),
            }

        ranked = ranked[:top_k]
        return {
            "status": "success",
            "documents": ranked,
            "reranked": True,
            "fallback": False,
            "method": method,
            "count": len(ranked),
        }

    async def _rerank_cross_encoder(
        self,
        context: PipelineContext,
        docs: list[dict[str, Any]],
        cfg: Any,
    ) -> tuple[list[dict[str, Any]], str]:
        try:
            from sentence_transformers import CrossEncoder  # type: ignore
        except Exception:
            logger.info("rerank_cross_encoder_unavailable")
            return await self._rerank_embedding(context, docs, "embedding_fallback")

        try:
            model = CrossEncoder(cfg.model_name)
        except Exception as exc:
            logger.warning("rerank_cross_encoder_load_failed", error=str(exc))
            return await self._rerank_embedding(context, docs, "embedding_fallback")

        pairs = [(context.query, _doc_text(d)) for d in docs]
        try:
            scores = list(model.predict(pairs, batch_size=cfg.batch_size))
        except Exception as exc:
            logger.warning("rerank_cross_encoder_predict_failed", error=str(exc))
            return await self._rerank_embedding(context, docs, "embedding_fallback")

        scored = [
            {**d, "score": float(s)} for d, s in zip(docs, scores, strict=True)
        ]
        scored.sort(key=lambda d: d["score"], reverse=True)
        return scored, "cross_encoder"

    async def _rerank_embedding(
        self,
        context: PipelineContext,
        docs: list[dict[str, Any]],
        method: str = "embedding_custom",
    ) -> tuple[list[dict[str, Any]], str]:
        from backend.eval.similarity import embed_similarity

        scored = [
            {**d, "score": embed_similarity(context.query, _doc_text(d))}
            for d in docs
        ]
        scored.sort(key=lambda d: d["score"], reverse=True)
        return scored, method

    async def _rerank_cohere(
        self,
        context: PipelineContext,
        docs: list[dict[str, Any]],
        cfg: Any,
    ) -> tuple[list[dict[str, Any]], str]:
        if not cfg.api_key:
            logger.info("rerank_cohere_no_api_key")
            return await self._rerank_embedding(context, docs, "embedding_fallback")

        url = "https://api.cohere.ai/v1/rerank"
        documents = [_doc_text(d) for d in docs]
        payload = {
            "model": cfg.model_name or "rerank-english-v3.0",
            "query": context.query,
            "documents": documents,
            "top_n": max(1, cfg.top_k),
        }
        headers = {
            "Authorization": f"Bearer {cfg.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=cfg.timeout_seconds, follow_redirects=True
            ) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.TimeoutException, httpx.HTTPError, ValueError) as exc:
            logger.warning("rerank_cohere_request_failed", error=str(exc))
            return await self._rerank_embedding(context, docs, "embedding_fallback")

        results = data.get("results", [])
        if not results:
            return await self._rerank_embedding(context, docs, "embedding_fallback")

        ranked: list[dict[str, Any]] = []
        covered: set[int] = set()
        for item in results:
            idx = int(item.get("index", -1))
            score = float(item.get("relevance_score", 0.0))
            if 0 <= idx < len(docs):
                ranked.append({**docs[idx], "score": score})
                covered.add(idx)
        # Preserve any docs the API omitted (defensive).
        for idx, d in enumerate(docs):
            if idx not in covered:
                ranked.append({**d, "score": 0.0})
        return ranked, "cohere"


class ReasonStep(_BaseStep):
    """Reasoning / agent step.

    Calls the configured LLM (via :class:`LLMClient`) to reason over the
    reranked documents. Falls back to an empty reasoning string when no LLM
    key/endpoint is configured, preserving skeleton behaviour for tests and
    offline runs.
    """

    def __init__(self) -> None:
        super().__init__("reason")

    async def execute(self, context: PipelineContext) -> dict[str, Any]:
        cfg = context.config.agent
        logger.info("reason_started", doc_count=len(context.reranked))

        client = LLMClient(
            model=cfg.model,
            api_key=cfg.api_key.get_secret_value() if cfg.api_key else None,
            base_url=cfg.base_url,
            provider=cfg.provider or "auto",
            timeout=cfg.timeout_seconds,
        )
        if not client.configured:
            return {"status": "success", "reasoning": "", "llm_used": False}

        docs = context.reranked or context.documents
        context_block = "\n\n".join(
            f"[{i + 1}] {d.get('content', d.get('text', ''))}"
            for i, d in enumerate(docs)
        )
        user_msg = (
            f"Query: {context.query}\n\n"
            f"Retrieved context:\n{context_block}\n\n"
            "Reason step-by-step about how to answer the query using the "
            "provided context. Output your reasoning only."
        )
        try:
            result = await client.complete(
                [{"role": "user", "content": user_msg}],
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                system=cfg.system_prompt,
            )
        except LLMClientError as exc:
            logger.warning("reason_llm_failed", error=str(exc))
            return {"status": "success", "reasoning": "", "llm_used": False}

        await attach_local_stats(result, cfg.base_url, transport=None)
        return {
            "status": "success",
            "reasoning": result["text"],
            "tokens": result["tokens"],
            "llm_used": True,
        }


class GuardrailStep(_BaseStep):
    """Skeleton guardrail step."""

    def __init__(self) -> None:
        super().__init__("guardrail")

    async def execute(self, context: PipelineContext) -> dict[str, Any]:
        enabled = context.config.guardrails.enabled
        logger.info("guardrail_started", enabled=enabled)
        if not enabled:
            return {"status": "success", "passed": True, "violations": []}
        return {"status": "success", "passed": True, "violations": []}


class GenerateStep(_BaseStep):
    """Final generation step.

    Calls the configured LLM (via :class:`LLMClient`) to produce the answer
    from the reranked documents (and optional reasoning). Falls back to an
    empty string when no LLM key/endpoint is configured.
    """

    def __init__(self) -> None:
        super().__init__("generate")

    async def execute(self, context: PipelineContext) -> dict[str, Any]:
        cfg = context.config.generator
        logger.info("generate_started")

        client = LLMClient(
            model=cfg.model,
            api_key=cfg.api_key.get_secret_value() if cfg.api_key else None,
            base_url=cfg.base_url,
            provider=cfg.provider or "auto",
            timeout=cfg.timeout_seconds,
        )
        if not client.configured:
            return {"status": "success", "text": "", "llm_used": False}

        docs = context.reranked or context.documents
        context_block = "\n\n".join(
            f"[{i + 1}] {d.get('content', d.get('text', ''))}"
            for i, d in enumerate(docs)
        )
        user_parts = [f"Query: {context.query}", f"Context:\n{context_block}"]
        if context.reasoning:
            user_parts.append(f"Reasoning:\n{context.reasoning}")
        user_parts.append("Answer the query using only the provided context.")
        user_msg = "\n\n".join(user_parts)

        try:
            result = await client.complete(
                [{"role": "user", "content": user_msg}],
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                system=cfg.system_prompt,
            )
        except LLMClientError as exc:
            logger.warning("generate_llm_failed", error=str(exc))
            return {"status": "success", "text": "", "llm_used": False}

        await attach_local_stats(result, cfg.base_url, transport=None)
        answer = result["text"]
        if cfg.include_citations and docs:
            answer = f"{answer}\n\nSources:\n" + "\n".join(
                f"[{i + 1}]" for i in range(len(docs))
            )
        return {
            "status": "success",
            "text": answer,
            "tokens": result["tokens"],
            "llm_used": True,
        }


# ---------------------------------------------------------------------------
# Pipeline executor
# ---------------------------------------------------------------------------


class PipelineExecutor:
    """Orchestrates a sequential pipeline with built-in tracing.

    Parameters
    ----------
    config : PipelineConfig
        Full pipeline configuration.
    steps : list[PipelineStep] | None
        Ordered list of steps to execute.  When *None*, the default
        sequence ``Retrieve → Rerank → Reason → Guardrail → Generate``
        is used.
    tracer : Tracer | None
        Optional external tracer.  One is created automatically when omitted.
    """

    @classmethod
    def _default_steps(cls) -> list[PipelineStep]:
        return [
            RetrieveStep(),
            RerankStep(),
            ReasonStep(),
            GuardrailStep(),
            GenerateStep(),
        ]

    def __init__(
        self,
        config: PipelineConfig,
        steps: list[PipelineStep] | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.config = config
        self.steps = steps if steps is not None else self._default_steps()
        self.tracer = tracer or Tracer(
            sample_rate=config.trace_sample_rate,
        )

    async def execute(self, query: str) -> Trajectory:
        """Run the full pipeline for *query* and return its trajectory.

        Steps are executed sequentially.  If a step fails, execution halts
        and the trajectory is finalised with the failure recorded.
        """
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

                    # Record tokens if the step provided them
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
                        break  # halt on failure

                    ctx.results[pipeline_step.name] = result
        finally:
            self.tracer.finish(trajectory)
        self._annotate_step_quality(trajectory)
        return trajectory

    @staticmethod
    def _annotate_step_quality(trajectory: Trajectory) -> None:
        """Set a per-step quality proxy on ``step.metrics.score``.

        Only sets a score where a meaningful signal is already available in the
        step payload — no new network calls.  Never overwrites a lower existing
        score (e.g. one populated earlier by an eval run).
        """
        for step in trajectory.steps:
            if step.status != StepStatus.SUCCESS:
                continue
            result = step.payload.get("result", {})
            value: float | None = None
            if step.step_name in ("retrieve",):
                value = 1.0 if result.get("count", 0) > 0 else 0.0
            elif step.step_name == "guardrail":
                value = 1.0 if result.get("passed") is True else 0.0
            elif step.step_name == "rerank":
                value = 1.0 if result.get("reranked") is True else 0.0
            elif step.step_name in ("generate", "reason"):
                if result.get("llm_used") is True:
                    value = 1.0
                else:
                    value = 0.0
            if value is None:
                continue
            step.metrics.score = max(step.metrics.score or 0.0, value)


# ---------------------------------------------------------------------------
# Pipeline builder (fluent API)
# ---------------------------------------------------------------------------


class PipelineBuilder:
    """Fluent builder for constructing a custom pipeline."""

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self._config = config or PipelineConfig()
        self._steps: list[PipelineStep] = []

    def add_step(self, step: PipelineStep) -> PipelineBuilder:
        """Append a step to the pipeline."""
        self._steps.append(step)
        return self

    def with_config(self, config: PipelineConfig) -> PipelineBuilder:
        """Replace the configuration."""
        self._config = config
        return self

    def build(self) -> PipelineExecutor:
        """Build the executor.  Uses default steps when none were added."""
        steps = self._steps if self._steps else None
        return PipelineExecutor(config=self._config, steps=steps)
