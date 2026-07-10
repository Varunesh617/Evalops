"""Pipeline configuration models using Pydantic settings."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, SecretStr


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RetrievalStrategy(StrEnum):
    """Retrieval backend strategy."""

    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"


class RerankerModel(StrEnum):
    """Supported reranker backends."""

    CROSS_ENCODER = "cross_encoder"
    COHERE = "cohere"
    CUSTOM = "custom"


class GuardrailSeverity(StrEnum):
    """Severity level for guardrail violations."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class StepStatus(StrEnum):
    """Execution status of a pipeline step."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


class RetrievalConfig(BaseModel):
    """Configuration for the retrieval step."""

    strategy: RetrievalStrategy = RetrievalStrategy.HYBRID
    top_k: Annotated[int, Field(ge=1, le=1000)] = 20
    index_name: str = "default"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: Annotated[int, Field(ge=1, le=4096)] = 1536
    similarity_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.7
    timeout_seconds: Annotated[float, Field(ge=0.1)] = 30.0
    database_url: SecretStr = SecretStr("postgresql://localhost/evalops")


class RerankerConfig(BaseModel):
    """Configuration for the reranker step."""

    model: RerankerModel = RerankerModel.CROSS_ENCODER
    top_k: Annotated[int, Field(ge=1, le=500)] = 10
    model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    batch_size: Annotated[int, Field(ge=1, le=128)] = 32
    timeout_seconds: Annotated[float, Field(ge=0.1)] = 15.0
    api_key: SecretStr | None = None


class AgentConfig(BaseModel):
    """Configuration for the reasoning / agent step."""

    model: str = "gpt-4o"
    max_tokens: Annotated[int, Field(ge=1, le=128_000)] = 4096
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.7
    system_prompt: str = "You are a helpful assistant."
    max_tool_calls: Annotated[int, Field(ge=0, le=50)] = 5
    timeout_seconds: Annotated[float, Field(ge=0.1)] = 60.0
    api_key: SecretStr | None = None


class GuardrailFilterConfig(BaseModel):
    """Configuration for an individual guardrail filter."""

    name: str
    enabled: bool = True
    severity: GuardrailSeverity = GuardrailSeverity.MEDIUM
    threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.8
    timeout_seconds: Annotated[float, Field(ge=0.1)] = 10.0


class GuardrailConfig(BaseModel):
    """Configuration for the guardrail stack."""

    enabled: bool = True
    fail_open: bool = False  # if True, allow through on error
    filters: list[GuardrailFilterConfig] = Field(default_factory=list)
    timeout_seconds: Annotated[float, Field(ge=0.1)] = 30.0


class GeneratorConfig(BaseModel):
    """Configuration for the final generation step."""

    model: str = "gpt-4o"
    max_tokens: Annotated[int, Field(ge=1, le=128_000)] = 4096
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.3
    system_prompt: str = "Answer the user's question using only the provided context."
    timeout_seconds: Annotated[float, Field(ge=0.1)] = 60.0
    api_key: SecretStr | None = None
    include_citations: bool = True


# ---------------------------------------------------------------------------
# Top-level pipeline config
# ---------------------------------------------------------------------------


class PipelineConfig(BaseModel):
    """Top-level configuration for an EvalOps pipeline run."""

    pipeline_id: str = "default"
    version: str = "1.0.0"

    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    reranker: RerankerConfig = Field(default_factory=RerankerConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    guardrails: GuardrailConfig = Field(default_factory=GuardrailConfig)
    generator: GeneratorConfig = Field(default_factory=GeneratorConfig)

    max_retries: Annotated[int, Field(ge=0, le=10)] = 2
    total_timeout_seconds: Annotated[float, Field(ge=1.0)] = 180.0
    enable_tracing: bool = True
    trace_sample_rate: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
