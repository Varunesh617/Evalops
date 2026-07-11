"""Tests for Pydantic config models in backend.core.config."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from backend.core.config import (
    AgentConfig,
    GeneratorConfig,
    GuardrailConfig,
    GuardrailFilterConfig,
    GuardrailSeverity,
    PipelineConfig,
    RerankerConfig,
    RerankerModel,
    RetrievalConfig,
    RetrievalStrategy,
    StepStatus,
)


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------


class TestRetrievalStrategy:
    def test_values(self):
        assert RetrievalStrategy.DENSE == "dense"
        assert RetrievalStrategy.SPARSE == "sparse"
        assert RetrievalStrategy.HYBRID == "hybrid"

    def test_membership(self):
        assert len(RetrievalStrategy) == 3


class TestRerankerModel:
    def test_values(self):
        assert RerankerModel.CROSS_ENCODER == "cross_encoder"
        assert RerankerModel.COHERE == "cohere"
        assert RerankerModel.CUSTOM == "custom"


class TestGuardrailSeverity:
    def test_ordering(self):
        vals = [s.value for s in GuardrailSeverity]
        assert vals == ["low", "medium", "high", "critical"]


class TestStepStatus:
    def test_all_statuses(self):
        expected = {"pending", "running", "success", "failed", "skipped", "timed_out"}
        assert {s.value for s in StepStatus} == expected


# ---------------------------------------------------------------------------
# RetrievalConfig tests
# ---------------------------------------------------------------------------


class TestRetrievalConfig:
    def test_defaults(self):
        cfg = RetrievalConfig()
        assert cfg.strategy == RetrievalStrategy.HYBRID
        assert cfg.top_k == 20
        assert cfg.index_name == "default"
        assert cfg.embedding_model == "text-embedding-3-small"
        assert cfg.embedding_dim == 1536
        assert cfg.similarity_threshold == 0.7
        assert cfg.timeout_seconds == 30.0
        assert isinstance(cfg.database_url, SecretStr)

    def test_custom_values(self):
        cfg = RetrievalConfig(
            strategy=RetrievalStrategy.DENSE,
            top_k=50,
            similarity_threshold=0.9,
        )
        assert cfg.strategy == RetrievalStrategy.DENSE
        assert cfg.top_k == 50
        assert cfg.similarity_threshold == 0.9

    def test_top_k_out_of_range(self):
        with pytest.raises(ValidationError):
            RetrievalConfig(top_k=0)
        with pytest.raises(ValidationError):
            RetrievalConfig(top_k=1001)

    def test_similarity_threshold_out_of_range(self):
        with pytest.raises(ValidationError):
            RetrievalConfig(similarity_threshold=-0.1)
        with pytest.raises(ValidationError):
            RetrievalConfig(similarity_threshold=1.1)

    def test_embedding_dim_bounds(self):
        with pytest.raises(ValidationError):
            RetrievalConfig(embedding_dim=0)
        with pytest.raises(ValidationError):
            RetrievalConfig(embedding_dim=5000)

    def test_timeout_must_be_positive(self):
        with pytest.raises(ValidationError):
            RetrievalConfig(timeout_seconds=0.0)


# ---------------------------------------------------------------------------
# RerankerConfig tests
# ---------------------------------------------------------------------------


class TestRerankerConfig:
    def test_defaults(self):
        cfg = RerankerConfig()
        assert cfg.model == RerankerModel.CROSS_ENCODER
        assert cfg.top_k == 10
        assert cfg.batch_size == 32
        assert cfg.api_key is None

    def test_custom_api_key(self):
        cfg = RerankerConfig(api_key=SecretStr("sk-test"))
        assert cfg.api_key is not None
        assert cfg.api_key.get_secret_value() == "sk-test"

    def test_batch_size_bounds(self):
        with pytest.raises(ValidationError):
            RerankerConfig(batch_size=0)
        with pytest.raises(ValidationError):
            RerankerConfig(batch_size=200)


# ---------------------------------------------------------------------------
# AgentConfig tests
# ---------------------------------------------------------------------------


class TestAgentConfig:
    def test_defaults(self):
        cfg = AgentConfig()
        assert cfg.model == "gpt-4o"
        assert cfg.max_tokens == 4096
        assert cfg.temperature == 0.7
        assert cfg.max_tool_calls == 5

    def test_temperature_bounds(self):
        with pytest.raises(ValidationError):
            AgentConfig(temperature=-0.1)
        with pytest.raises(ValidationError):
            AgentConfig(temperature=2.1)

    def test_max_tokens_bounds(self):
        with pytest.raises(ValidationError):
            AgentConfig(max_tokens=0)
        with pytest.raises(ValidationError):
            AgentConfig(max_tokens=200_000)


# ---------------------------------------------------------------------------
# GuardrailFilterConfig tests
# ---------------------------------------------------------------------------


class TestGuardrailFilterConfig:
    def test_required_fields(self):
        cfg = GuardrailFilterConfig(name="test_filter")
        assert cfg.name == "test_filter"
        assert cfg.enabled is True
        assert cfg.severity == GuardrailSeverity.MEDIUM
        assert cfg.threshold == 0.8

    def test_disabled(self):
        cfg = GuardrailFilterConfig(name="filter", enabled=False)
        assert cfg.enabled is False

    def test_threshold_bounds(self):
        with pytest.raises(ValidationError):
            GuardrailFilterConfig(name="f", threshold=-0.1)
        with pytest.raises(ValidationError):
            GuardrailFilterConfig(name="f", threshold=1.1)


# ---------------------------------------------------------------------------
# GuardrailConfig tests
# ---------------------------------------------------------------------------


class TestGuardrailConfig:
    def test_defaults(self):
        cfg = GuardrailConfig()
        assert cfg.enabled is True
        assert cfg.fail_open is False
        assert cfg.filters == []
        assert cfg.timeout_seconds == 30.0

    def test_with_filters(self):
        filters = [
            GuardrailFilterConfig(name="f1"),
            GuardrailFilterConfig(name="f2", threshold=0.5),
        ]
        cfg = GuardrailConfig(filters=filters)
        assert len(cfg.filters) == 2

    def test_fail_open(self):
        cfg = GuardrailConfig(fail_open=True)
        assert cfg.fail_open is True


# ---------------------------------------------------------------------------
# GeneratorConfig tests
# ---------------------------------------------------------------------------


class TestGeneratorConfig:
    def test_defaults(self):
        cfg = GeneratorConfig()
        assert cfg.model == "gpt-4o"
        assert cfg.max_tokens == 4096
        assert cfg.temperature == 0.3
        assert cfg.include_citations is True

    def test_custom(self):
        cfg = GeneratorConfig(model="gpt-4o-mini", include_citations=False)
        assert cfg.model == "gpt-4o-mini"
        assert cfg.include_citations is False


# ---------------------------------------------------------------------------
# PipelineConfig (top-level) tests
# ---------------------------------------------------------------------------


class TestPipelineConfig:
    def test_defaults(self):
        cfg = PipelineConfig()
        assert cfg.pipeline_id == "default"
        assert cfg.version == "1.0.0"
        assert cfg.max_retries == 2
        assert cfg.total_timeout_seconds == 180.0
        assert cfg.enable_tracing is True
        assert cfg.trace_sample_rate == 1.0

    def test_sub_configs_created(self):
        cfg = PipelineConfig()
        assert isinstance(cfg.retrieval, RetrievalConfig)
        assert isinstance(cfg.reranker, RerankerConfig)
        assert isinstance(cfg.agent, AgentConfig)
        assert isinstance(cfg.guardrails, GuardrailConfig)
        assert isinstance(cfg.generator, GeneratorConfig)

    def test_max_retries_bounds(self):
        with pytest.raises(ValidationError):
            PipelineConfig(max_retries=-1)
        with pytest.raises(ValidationError):
            PipelineConfig(max_retries=11)

    def test_total_timeout_bounds(self):
        with pytest.raises(ValidationError):
            PipelineConfig(total_timeout_seconds=0.5)

    def test_trace_sample_rate_bounds(self):
        with pytest.raises(ValidationError):
            PipelineConfig(trace_sample_rate=-0.1)
        with pytest.raises(ValidationError):
            PipelineConfig(trace_sample_rate=1.1)

    def test_json_serialization_roundtrip(self, custom_pipeline_config):
        data = custom_pipeline_config.model_dump()
        restored = PipelineConfig(**data)
        assert restored.pipeline_id == custom_pipeline_config.pipeline_id
        assert restored.retrieval.strategy == custom_pipeline_config.retrieval.strategy

    def test_custom_full_config(self, custom_pipeline_config):
        assert custom_pipeline_config.pipeline_id == "test-pipeline"
        assert custom_pipeline_config.retrieval.strategy == RetrievalStrategy.DENSE
        assert custom_pipeline_config.retrieval.top_k == 10
        assert custom_pipeline_config.agent.model == "gpt-4o-mini"
        assert len(custom_pipeline_config.guardrails.filters) == 1
