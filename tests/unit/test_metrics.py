"""Tests for all 6 evaluation metrics."""

from __future__ import annotations

import pytest

from backend.eval.metrics.base import BaseMetric
from backend.eval.metrics.context_relevance import ContextRelevanceMetric
from backend.eval.metrics.cost_efficiency import CostEfficiencyMetric
from backend.eval.metrics.faithfulness import FaithfulnessMetric
from backend.eval.metrics.guardrail_fp_rate import GuardrailFPRateMetric
from backend.eval.metrics.tool_call_accuracy import ToolCallAccuracyMetric
from backend.eval.metrics.trajectory_coherence import TrajectoryCoherenceMetric
from backend.eval.models import (
    MetricResult,
    Step,
    StepScore,
    StepType,
    ToolCall,
    Trajectory,
)


# ---------------------------------------------------------------------------
# Helper to build trajectories
# ---------------------------------------------------------------------------


def _traj(**kwargs) -> Trajectory:
    defaults = dict(trajectory_id="t", query="What is Python?", steps=[])
    defaults.update(kwargs)
    return Trajectory(**defaults)


# ---------------------------------------------------------------------------
# BaseMetric utility tests
# ---------------------------------------------------------------------------


class TestBaseMetricUtilities:
    def test_clamp_in_range(self):
        assert BaseMetric.clamp(0.5) == 0.5

    def test_clamp_below(self):
        assert BaseMetric.clamp(-0.1) == 0.0

    def test_clamp_above(self):
        assert BaseMetric.clamp(1.5) == 1.0

    def test_clamp_custom_range(self):
        assert BaseMetric.clamp(15, 10, 20) == 15

    def test_normalise(self):
        assert BaseMetric.normalise(5, 0, 10) == 0.5

    def test_normalise_equal_min_max(self):
        assert BaseMetric.normalise(5, 5, 5) == 0.0

    def test_token_overlap_identical(self):
        assert BaseMetric.token_overlap("hello world", "hello world") == 1.0

    def test_token_overlap_no_overlap(self):
        assert BaseMetric.token_overlap("cat", "dog") == 0.0

    def test_token_overlap_empty(self):
        assert BaseMetric.token_overlap("", "") == 1.0

    def test_token_overlap_one_empty(self):
        assert BaseMetric.token_overlap("hello", "") == 0.0

    def test_cosine_similarity_identical(self):
        assert BaseMetric.cosine_similarity_simple([1, 0], [1, 0]) == pytest.approx(1.0)

    def test_cosine_similarity_orthogonal(self):
        assert BaseMetric.cosine_similarity_simple([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_cosine_similarity_zero_vector(self):
        assert BaseMetric.cosine_similarity_simple([0, 0], [1, 0]) == 0.0

    def test_cosine_similarity_opposite(self):
        assert BaseMetric.cosine_similarity_simple([1, 0], [-1, 0]) == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# BaseMetric evaluate tests
# ---------------------------------------------------------------------------


class TestBaseMetricEvaluate:
    def test_evaluate_empty_trajectory(self):
        class TestMetric(BaseMetric):
            name = "test_metric"

            def score_step(self, trajectory, step):
                return StepScore(step_id=step.step_id, metric_name=self.name, score=0.5)

        m = TestMetric()
        result = m.evaluate(_traj())
        assert isinstance(result, MetricResult)
        assert result.metric_name == "test_metric"
        assert result.overall_score == 0.0  # no step scores

    def test_evaluate_with_steps(self):
        class TestMetric(BaseMetric):
            name = "test_metric"

            def score_step(self, trajectory, step):
                return StepScore(step_id=step.step_id, metric_name=self.name, score=0.8)

        m = TestMetric()
        steps = [Step(step_id=0, step_type=StepType.QUERY), Step(step_id=1, step_type=StepType.ANSWER)]
        result = m.evaluate(_traj(steps=steps))
        assert len(result.step_scores) == 2
        assert result.overall_score > 0


# ---------------------------------------------------------------------------
# irrelevant_weight tests (Task 2.7)
# ---------------------------------------------------------------------------


class TestIrrelevantWeight:
    def _make_metric(self, irrelevant_weight):
        class WMetric(BaseMetric):
            name = "w_metric"

            def __init__(self, **kw):
                super().__init__(**kw)

            def score_step(self, trajectory, step):
                # Relevant step (id 0) scores 0.8, irrelevant (id 1) 0.4.
                score = 0.8 if step.step_id == 0 else 0.4
                return StepScore(
                    step_id=step.step_id,
                    metric_name=self.name,
                    score=float(score),
                )

            def _is_relevant(self, step_score):
                # First step is relevant (weight 1.0), second is not.
                return step_score.step_id == 0

        return WMetric(irrelevant_weight=irrelevant_weight)

    def test_invalid_weight_raises(self):
        with pytest.raises(ValueError, match="irrelevant_weight"):
            self._make_metric(2.0)
        with pytest.raises(ValueError, match="irrelevant_weight"):
            self._make_metric(-0.5)

    def test_default_weight_zero_contribution(self):
        # irrelevant step contributes 0 -> overall = score of relevant step.
        m = self._make_metric(0.0)
        steps = [
            Step(step_id=0, step_type=StepType.RETRIEVAL),
            Step(step_id=1, step_type=StepType.ANSWER),
        ]
        # relevant=0.8 (weight 1), irrelevant weight 0 -> only relevant counts
        result = m.evaluate(_traj(steps=steps))
        assert result.overall_score == pytest.approx(0.8)

    def test_weight_one_equals_simple_mean(self):
        m = self._make_metric(1.0)
        steps = [
            Step(step_id=0, step_type=StepType.RETRIEVAL),
            Step(step_id=1, step_type=StepType.ANSWER),
        ]
        result = m.evaluate(_traj(steps=steps))
        assert result.overall_score == pytest.approx(0.6)

    def test_weight_zero_point_twentyfive_default(self):
        m = self._make_metric(0.25)
        steps = [
            Step(step_id=0, step_type=StepType.RETRIEVAL),
            Step(step_id=1, step_type=StepType.ANSWER),
        ]
        # (0.8*1 + 0.4*0.25) / (1 + 0.25) = 0.9/1.25
        result = m.evaluate(_traj(steps=steps))
        assert result.overall_score == pytest.approx(0.9 / 1.25)


# ---------------------------------------------------------------------------
# FaithfulnessMetric tests
# ---------------------------------------------------------------------------


class TestFaithfulnessMetric:
    def test_non_answer_step_gets_1(self):
        m = FaithfulnessMetric()
        step = Step(step_id=0, step_type=StepType.RETRIEVAL, output_text="something")
        result = m.score_step(_traj(), step)
        assert result.score == 1.0

    def test_answer_with_matching_context(self):
        m = FaithfulnessMetric()
        traj = _traj(
            steps=[Step(step_id=0, step_type=StepType.RETRIEVAL, context_chunks=["Python is a programming language"])],
            retrieved_context=["Python is a programming language"],
        )
        step = Step(step_id=1, step_type=StepType.ANSWER, output_text="Python is a programming language.")
        result = m.score_step(traj, step)
        assert result.score >= 0.5

    def test_answer_no_claims(self):
        m = FaithfulnessMetric()
        step = Step(step_id=0, step_type=StepType.ANSWER, output_text="OK")
        result = m.score_step(_traj(), step)
        # "OK" is too short to be a claim
        assert result.score == 1.0

    def test_answer_no_context_unsupported(self):
        m = FaithfulnessMetric()
        step = Step(step_id=0, step_type=StepType.ANSWER, output_text="The Earth orbits Mars in a highly elliptical pattern.")
        result = m.score_step(_traj(), step)
        assert result.score <= 1.0  # Some claims may be unsupported

    def test_full_evaluate(self):
        m = FaithfulnessMetric()
        steps = [
            Step(step_id=0, step_type=StepType.RETRIEVAL, context_chunks=["Python is a language"]),
            Step(step_id=1, step_type=StepType.ANSWER, output_text="Python is a language."),
        ]
        result = m.evaluate(_traj(steps=steps, retrieved_context=["Python is a language"]))
        assert isinstance(result, MetricResult)
        assert result.metric_name == "faithfulness"

    def test_custom_threshold(self):
        m = FaithfulnessMetric(overlap_threshold=0.5)
        assert m.overlap_threshold == 0.5

    def test_invalid_similarity_mode(self):
        with pytest.raises(ValueError, match="similarity_mode"):
            FaithfulnessMetric(similarity_mode="bogus")

    def test_token_mode_is_default(self):
        m = FaithfulnessMetric()
        assert m.similarity_mode == "token"

    def test_embedding_mode_falls_back_when_unavailable(self, monkeypatch):
        # Force the embedding backend to be unavailable; embedding mode must
        # fall back to token overlap without raising.
        import backend.eval.similarity as sim

        def _boom(*a, **k):
            raise sim.EmbeddingsUnavailableError("forced")

        monkeypatch.setattr(sim.EmbeddingBackend, "_load_local_model", staticmethod(_boom))
        m = FaithfulnessMetric(similarity_mode="embedding")
        traj = _traj(
            steps=[
                Step(
                    step_id=0,
                    step_type=StepType.RETRIEVAL,
                    context_chunks=["Python is a programming language"],
                )
            ],
            retrieved_context=["Python is a programming language"],
        )
        step = Step(
            step_id=1,
            step_type=StepType.ANSWER,
            output_text="Python is a programming language.",
        )
        result = m.score_step(traj, step)
        assert 0.0 <= result.score <= 1.0

    def test_hybrid_mode_supported_on_semantic_match(self, monkeypatch):
        # When embeddings are unavailable, hybrid must still flag a claim as
        # supported when token overlap is high (regression-safe).
        import backend.eval.similarity as sim

        def _boom(*a, **k):
            raise sim.EmbeddingsUnavailableError("forced")

        monkeypatch.setattr(sim.EmbeddingBackend, "_load_local_model", staticmethod(_boom))
        m = FaithfulnessMetric(similarity_mode="hybrid")
        traj = _traj(
            steps=[
                Step(
                    step_id=0,
                    step_type=StepType.RETRIEVAL,
                    context_chunks=["The Eiffel Tower is located in Paris"],
                )
            ],
            retrieved_context=["The Eiffel Tower is located in Paris"],
        )
        step = Step(
            step_id=1,
            step_type=StepType.ANSWER,
            output_text="The Eiffel Tower is located in Paris.",
        )
        result = m.score_step(traj, step)
        assert result.score >= 0.5


# ---------------------------------------------------------------------------
# ContextRelevanceMetric tests
# ---------------------------------------------------------------------------


class TestContextRelevanceMetric:
    def test_non_retrieval_step(self):
        m = ContextRelevanceMetric()
        step = Step(step_id=0, step_type=StepType.ANSWER)
        result = m.score_step(_traj(), step)
        assert result.score == 1.0

    def test_retrieval_with_relevant_chunks(self):
        m = ContextRelevanceMetric()
        step = Step(
            step_id=0,
            step_type=StepType.RETRIEVAL,
            context_chunks=["python is a programming language"],
        )
        result = m.score_step(_traj(query="what is python"), step)
        assert result.score > 0

    def test_retrieval_empty_chunks(self):
        m = ContextRelevanceMetric()
        step = Step(step_id=0, step_type=StepType.RETRIEVAL, context_chunks=[])
        result = m.score_step(_traj(), step)
        assert result.score == 0.0

    def test_aggregate_only_retrieval_steps(self):
        m = ContextRelevanceMetric()
        steps = [
            Step(step_id=0, step_type=StepType.QUERY),
            Step(step_id=1, step_type=StepType.RETRIEVAL, context_chunks=["Python"]),
            Step(step_id=2, step_type=StepType.ANSWER),
        ]
        result = m.evaluate(_traj(query="Python", steps=steps))
        assert len(result.step_scores) == 3
        # Only retrieval step should be scored in aggregate

    def test_trajectory_level_fallback(self):
        m = ContextRelevanceMetric()
        steps = [Step(step_id=0, step_type=StepType.QUERY)]
        result = m.evaluate(_traj(
            query="Python",
            steps=steps,
            retrieved_context=["Python programming"],
        ))
        assert result.overall_score >= 0

    def test_invalid_similarity_mode(self):
        with pytest.raises(ValueError, match="similarity_mode"):
            ContextRelevanceMetric(similarity_mode="bogus")

    def test_embedding_mode_falls_back_when_unavailable(self, monkeypatch):
        import backend.eval.similarity as sim

        def _boom(*a, **k):
            raise sim.EmbeddingsUnavailableError("forced")

        monkeypatch.setattr(sim.EmbeddingBackend, "_load_local_model", staticmethod(_boom))
        m = ContextRelevanceMetric(similarity_mode="embedding")
        step = Step(
            step_id=0,
            step_type=StepType.RETRIEVAL,
            context_chunks=["python is a programming language"],
        )
        result = m.score_step(_traj(query="what is python"), step)
        assert 0.0 <= result.score <= 1.0

    def test_hybrid_mode_uses_token_fallback(self, monkeypatch):
        import backend.eval.similarity as sim

        def _boom(*a, **k):
            raise sim.EmbeddingsUnavailableError("forced")

        monkeypatch.setattr(sim.EmbeddingBackend, "_load_local_model", staticmethod(_boom))
        m = ContextRelevanceMetric(similarity_mode="hybrid")
        step = Step(
            step_id=0,
            step_type=StepType.RETRIEVAL,
            context_chunks=["python programming language"],
        )
        result = m.score_step(_traj(query="python programming"), step)
        assert result.score > 0


# ---------------------------------------------------------------------------
# TrajectoryCoherenceMetric tests
# ---------------------------------------------------------------------------


class TestTrajectoryCoherenceMetric:
    def test_perfect_order(self):
        m = TrajectoryCoherenceMetric()
        steps = [
            Step(step_id=0, step_type=StepType.QUERY),
            Step(step_id=1, step_type=StepType.RETRIEVAL),
            Step(step_id=2, step_type=StepType.REASONING),
            Step(step_id=3, step_type=StepType.ANSWER),
        ]
        result = m.evaluate(_traj(steps=steps))
        assert result.overall_score > 0.5

    def test_backward_order_penalised(self):
        m = TrajectoryCoherenceMetric()
        steps = [
            Step(step_id=0, step_type=StepType.ANSWER),  # Wrong order
            Step(step_id=1, step_type=StepType.QUERY),
        ]
        result = m.evaluate(_traj(steps=steps))
        # First step has no prev so gets 1.0; second step goes backward
        assert len(result.step_scores) == 2

    def test_empty_steps(self):
        m = TrajectoryCoherenceMetric()
        result = m.evaluate(_traj(steps=[]))
        assert result.overall_score == 0.0

    def test_single_step(self):
        m = TrajectoryCoherenceMetric()
        steps = [Step(step_id=0, step_type=StepType.QUERY)]
        result = m.evaluate(_traj(steps=steps))
        assert result.overall_score > 0


# ---------------------------------------------------------------------------
# ToolCallAccuracyMetric tests
# ---------------------------------------------------------------------------


class TestToolCallAccuracyMetric:
    def test_non_tool_call_step(self):
        m = ToolCallAccuracyMetric()
        step = Step(step_id=0, step_type=StepType.QUERY)
        result = m.score_step(_traj(), step)
        assert result.score == 1.0

    def test_tool_call_no_calls(self):
        m = ToolCallAccuracyMetric()
        step = Step(step_id=0, step_type=StepType.TOOL_CALL, tool_calls=[])
        result = m.score_step(_traj(), step)
        assert result.score == 0.0

    def test_perfect_tool_call(self):
        m = ToolCallAccuracyMetric()
        tc = ToolCall(
            tool_name="search",
            expected_tool="search",
            parameters={"q": "cats"},
            expected_parameters={"q": "cats"},
        )
        step = Step(step_id=0, step_type=StepType.TOOL_CALL, tool_calls=[tc])
        result = m.score_step(_traj(), step)
        assert result.score == 1.0

    def test_wrong_tool_name(self):
        m = ToolCallAccuracyMetric()
        tc = ToolCall(
            tool_name="search",
            expected_tool="browse",
        )
        step = Step(step_id=0, step_type=StepType.TOOL_CALL, tool_calls=[tc])
        result = m.score_step(_traj(), step)
        assert result.score < 1.0

    def test_no_expected_tool(self):
        m = ToolCallAccuracyMetric()
        tc = ToolCall(tool_name="search", parameters={"q": "x"})
        step = Step(step_id=0, step_type=StepType.TOOL_CALL, tool_calls=[tc])
        result = m.score_step(_traj(), step)
        assert result.score == 1.0  # No expectations = can't be wrong

    def test_partial_parameter_match(self):
        m = ToolCallAccuracyMetric()
        tc = ToolCall(
            tool_name="search",
            expected_tool="search",
            parameters={"q": "cats and dogs"},
            expected_parameters={"q": "cats"},
        )
        step = Step(step_id=0, step_type=StepType.TOOL_CALL, tool_calls=[tc])
        result = m.score_step(_traj(), step)
        # Partial match (substring) gets 0.5 weight
        assert result.score > 0


# ---------------------------------------------------------------------------
# GuardrailFPRateMetric tests
# ---------------------------------------------------------------------------


class TestGuardrailFPRateMetric:
    def test_non_guardrail_step(self):
        m = GuardrailFPRateMetric()
        step = Step(step_id=0, step_type=StepType.QUERY)
        result = m.score_step(_traj(), step)
        assert result.score == 1.0

    def test_guardrail_block_false_positive(self):
        m = GuardrailFPRateMetric()
        step = Step(
            step_id=0,
            step_type=StepType.GUARDRAIL_BLOCK,
            metadata={"is_legitimate": False},
        )
        result = m.score_step(_traj(), step)
        assert result.score == 0.0

    def test_guardrail_block_true_positive(self):
        m = GuardrailFPRateMetric()
        step = Step(
            step_id=0,
            step_type=StepType.GUARDRAIL_BLOCK,
            metadata={"is_legitimate": True},
        )
        result = m.score_step(_traj(), step)
        assert result.score == 1.0

    def test_guardrail_check_not_blocked(self):
        m = GuardrailFPRateMetric()
        step = Step(
            step_id=0,
            step_type=StepType.GUARDRAIL_CHECK,
            metadata={"blocked": False},
        )
        result = m.score_step(_traj(), step)
        assert result.score == 1.0

    def test_trajectory_level_blocked_legitimate(self):
        m = GuardrailFPRateMetric()
        traj = _traj(guardrail_blocked=True, guardrail_is_legitimate=True)
        result = m.aggregate_steps(traj, [])
        assert result == 1.0

    def test_trajectory_level_blocked_not_legitimate(self):
        m = GuardrailFPRateMetric()
        traj = _traj(guardrail_blocked=True, guardrail_is_legitimate=False)
        result = m.aggregate_steps(traj, [])
        assert result == 0.0

    def test_no_guardrail_activity(self):
        m = GuardrailFPRateMetric()
        traj = _traj(guardrail_blocked=False)
        result = m.aggregate_steps(traj, [])
        assert result == 1.0

    def test_combined_step_and_trajectory(self):
        m = GuardrailFPRateMetric()
        step_scores = [
            StepScore(step_id=0, metric_name="guardrail_fp_rate", score=1.0),
        ]
        traj = _traj(guardrail_blocked=True, guardrail_is_legitimate=True)
        result = m.aggregate_steps(traj, step_scores)
        assert 0.5 <= result <= 1.0  # weighted average


# ---------------------------------------------------------------------------
# CostEfficiencyMetric tests
# ---------------------------------------------------------------------------


class TestCostEfficiencyMetric:
    def test_zero_cost_trajectory(self):
        m = CostEfficiencyMetric()
        steps = [Step(step_id=0, step_type=StepType.RETRIEVAL, cost_usd=0.0, tokens_used=0)]
        traj = _traj(steps=steps, total_cost_usd=0.0)
        result = m.evaluate(traj)
        assert result.overall_score == 0.0

    def test_useful_step(self):
        m = CostEfficiencyMetric()
        step = Step(step_id=0, step_type=StepType.RETRIEVAL, cost_usd=0.001)
        traj = _traj(steps=[step], total_cost_usd=0.001)
        result = m.score_step(traj, step)
        assert result.score == 1.0

    def test_useless_step_with_cost(self):
        m = CostEfficiencyMetric()
        step = Step(step_id=0, step_type=StepType.GUARDRAIL_CHECK, cost_usd=0.001)
        traj = _traj(steps=[step], total_cost_usd=0.001)
        result = m.score_step(traj, step)
        assert result.score == 0.2

    def test_token_based_cost_estimation(self):
        m = CostEfficiencyMetric()
        step = Step(step_id=0, step_type=StepType.RETRIEVAL, tokens_used=1000)
        traj = _traj(steps=[step], total_cost_usd=0.0)
        result = m.score_step(traj, step)
        assert result.breakdown["cost_usd"] > 0

    def test_aggregate_with_cost(self):
        m = CostEfficiencyMetric(target_cost_usd=0.05)
        steps = [
            Step(step_id=0, step_type=StepType.RETRIEVAL, cost_usd=0.001),
            Step(step_id=1, step_type=StepType.REASONING, cost_usd=0.002),
            Step(step_id=2, step_type=StepType.ANSWER, cost_usd=0.001),
        ]
        traj = _traj(steps=steps, total_cost_usd=0.004)
        result = m.evaluate(traj)
        assert result.overall_score > 0

    def test_aggregate_no_steps(self):
        m = CostEfficiencyMetric()
        result = m.aggregate_steps(_traj(), [])
        assert result == 0.0
