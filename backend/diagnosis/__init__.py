"""Diagnosis module — counterfactual analysis, recommendations, and historical failure tracking."""

from __future__ import annotations

from backend.diagnosis.counterfactual import CounterfactualEngine, CounterfactualReport
from backend.diagnosis.historical_analyzer import HistoricalAnalyzer, HistoricalReport
from backend.diagnosis.recommender import Recommendation, RecommendationEngine

__all__ = [
    "CounterfactualEngine",
    "CounterfactualReport",
    "HistoricalAnalyzer",
    "HistoricalReport",
    "Recommendation",
    "RecommendationEngine",
]
