"""LLM-as-judge client — OpenAI-compatible chat-completions, optional.

The client is env-gated by ``EVALOPS_LLM_ENABLED`` (default ``"false"``). When
disabled, :attr:`LLMJudgeClient.enabled` is ``False`` and :meth:`judge` is
never called. All network access goes through ``httpx`` (already a base
dependency) — no new hard dependency is introduced. API keys are read via
``os.getenv`` and never logged.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


class LLMJudgeError(RuntimeError):
    """Raised when the LLM-as-judge request fails."""


_SYSTEM_PROMPT = (
    "You are an eval judge. Given a diagnosis rubric, score each dimension from "
    "0 to 1 (float) and return STRICT JSON of the form: "
    '{"scores": {"<dimension_name>": <float>}, "overall_rationale": "<str>"}. '
    "Do not include any text outside the JSON object."
)


class LLMJudgeClient:
    """Minimal OpenAI-compatible LLM judge."""

    def __init__(self) -> None:
        enabled_raw = os.getenv("EVALOPS_LLM_ENABLED", "false").lower()
        self.enabled: bool = enabled_raw in ("1", "true", "yes", "on")
        self.base_url: str = os.getenv(
            "EVALOPS_LLM_BASE_URL", "https://api.openai.com/v1"
        ).rstrip("/")
        self.api_key: str | None = os.getenv("EVALOPS_LLM_API_KEY")
        self.model: str = os.getenv("EVALOPS_LLM_MODEL", "gpt-4o-mini")
        self._log = logger.bind(component="llm_judge")

    def judge(
        self,
        rubric: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Score *rubric* via the LLM and return ``{"scores", "rationale"}``.

        Raises :class:`LLMJudgeError` on non-200, timeout, or JSON parse failure.
        """
        if not self.enabled:
            raise LLMJudgeError("LLM judge is disabled (EVALOPS_LLM_ENABLED != 'true')")
        if not self.api_key:
            raise LLMJudgeError("EVALOPS_LLM_API_KEY is not set")

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Score the following rubric:\n"
                        + json.dumps(rubric, default=str)
                    ),
                },
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException as exc:
            raise LLMJudgeError(f"LLM judge request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMJudgeError(f"LLM judge request failed: {exc}") from exc
        except ValueError as exc:  # json decode
            raise LLMJudgeError(f"LLM judge returned invalid JSON: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMJudgeError(f"Unexpected LLM judge response shape: {exc}") from exc

        scores = parsed.get("scores", {})
        rationale = parsed.get("overall_rationale", "")
        self._log.debug("llm_judgement_received", dimension_count=len(scores))
        return {"scores": scores, "rationale": rationale}

