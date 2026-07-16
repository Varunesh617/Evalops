"""Pipeline configuration models using Pydantic settings."""

from __future__ import annotations

import json
import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    base_url: str | None = None
    provider: str | None = None


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
    base_url: str | None = None
    provider: str | None = None
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


# ---------------------------------------------------------------------------
# LLM provider registry
# ---------------------------------------------------------------------------


class LLMProviderKind(StrEnum):
    """Supported LLM provider kinds for the server-side registry."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    OPENROUTER = "openrouter"
    CUSTOM = "custom"


class ProviderConfig(BaseModel):
    """A single registered LLM provider endpoint.

    ``name`` is the unique id used to look the provider up (e.g. ``"openai"``).
    The internal representation keeps the real ``api_key`` as a ``SecretStr``.
    """

    name: str
    kind: LLMProviderKind
    base_url: str
    api_key: SecretStr | None = None
    default_model: str = ""
    is_default: bool = False

    def safe_dump(self) -> dict[str, Any]:
        """Serialize for API responses, masking the api_key as ``"***"``."""
        data = self.model_dump()
        if self.api_key is not None:
            data["api_key"] = "***"
        return data


_DEFAULT_PROVIDERS: list[ProviderConfig] = [
    ProviderConfig(
        name="openai",
        kind=LLMProviderKind.OPENAI,
        base_url="https://api.openai.com/v1",
        is_default=True,
    ),
    ProviderConfig(
        name="anthropic",
        kind=LLMProviderKind.ANTHROPIC,
        base_url="https://api.anthropic.com/v1",
    ),
    ProviderConfig(
        name="ollama",
        kind=LLMProviderKind.OLLAMA,
        base_url="http://localhost:11434/v1",
    ),
    ProviderConfig(
        name="openrouter",
        kind=LLMProviderKind.OPENROUTER,
        base_url="https://openrouter.ai/api/v1",
    ),
]


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Server-level application settings (env prefix ``EVALOPS_``).

    Providers are persisted to a JSON file (``EVALOPS_PROVIDERS_FILE``, default
    ``<backend>/providers.json``). The providers file is the source of truth;
    env vars only override the scalar toggles.
    """

    model_config = SettingsConfigDict(
        env_prefix="EVALOPS_",
        extra="ignore",
    )

    providers_file: ClassVar[Path] = Path(
        os.getenv("EVALOPS_PROVIDERS_FILE", str(_PROJECT_ROOT / "providers.json"))
    )

    providers: list[ProviderConfig] = Field(default_factory=list)
    active_provider: str = "openai"
    llm_enabled: bool = False

    @classmethod
    def providers_path(cls) -> Path:
        """Resolve the providers JSON path.

        Resolution order: ``EVALOPS_PROVIDERS_FILE`` env var wins, then the
        ``providers_file`` class attribute (so tests/native exe can redirect
        persistence either way).
        """
        env_path = os.getenv("EVALOPS_PROVIDERS_FILE")
        if env_path:
            return Path(env_path)
        return cls.providers_file

    @classmethod
    def load_providers(cls) -> list[ProviderConfig]:
        """Read providers from disk, seeding defaults if the file is absent."""
        path = cls.providers_path()
        if not path.exists():
            providers = [p.model_copy(deep=True) for p in _DEFAULT_PROVIDERS]
            cls.save_providers(providers)
            return providers
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return [p.model_copy(deep=True) for p in _DEFAULT_PROVIDERS]
        return [ProviderConfig(**item) for item in raw]

    @classmethod
    def save_providers(cls, providers: list[ProviderConfig]) -> None:
        """Write providers to disk, preserving the real key in the file.

        The plaintext key is written to disk (the persisted store), never logged.
        """
        path = cls.providers_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = []
        for p in providers:
            item = p.model_dump()
            if p.api_key is not None:
                item["api_key"] = p.api_key.get_secret_value()
            data.append(item)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Lazily load and cache a :class:`Settings` singleton.

    Providers are populated from the providers file on first access.
    """
    settings = Settings()
    if not settings.providers:
        settings.providers = Settings.load_providers()
    return settings


# ---------------------------------------------------------------------------
# Plugin security config (signing)
# ---------------------------------------------------------------------------

DEFAULT_PLUGIN_PUBKEY_PATH = (
    Path(__file__).resolve().parent.parent / "plugins" / "keys" / "evalops_pub.pem"
)


class PluginSecurityConfig(BaseModel):
    """Environment-driven config for plugin cryptographic signing.

    Set ``EVALOPS_REQUIRE_SIGNED=true`` in production to block unsigned or
    unverifiable plugins.  When unset (dev mode), unsigned plugins only emit an
    advisory warning.
    """

    require_signed: bool = Field(
        default_factory=lambda: os.getenv("EVALOPS_REQUIRE_SIGNED", "false").lower()
        in {"1", "true", "yes"}
    )
    public_key_path: Path = Field(
        default_factory=lambda: Path(
            os.getenv("EVALOPS_PLUGIN_PUBKEY", str(DEFAULT_PLUGIN_PUBKEY_PATH))
        )
    )
