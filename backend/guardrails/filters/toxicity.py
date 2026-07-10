"""Toxicity filter with configurable threshold.

Uses category-weighted scoring across profanity, threats, hate speech,
sexual content, and self-harm indicators.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .base import BaseFilter, FilterDecision, FilterResult, RiskLevel


class ToxicityCategory(str, Enum):
    PROFANITY = "profanity"
    THREAT = "threat"
    HATE_SPEECH = "hate_speech"
    SEXUAL = "sexual"
    SELF_HARM = "self_harm"
    HARASSMENT = "harassment"
    VIOLENCE = "violence"


@dataclass(frozen=True, slots=True)
class ToxicityPattern:
    category: ToxicityCategory
    regex: re.Pattern[str]
    weight: float
    label: str


def _tp(cat: str, rx: str, w: float, label: str) -> ToxicityPattern:
    return ToxicityPattern(ToxicityCategory(cat), re.compile(rx, re.I), w, label)


PROFANITY_PATTERNS: list[ToxicityPattern] = [
    _tp("profanity", r"\b(f+[\W_]*u+[\W_]*c+[\W_]*k+)\b", 0.6, "profanity_explicit"),
    _tp("profanity", r"\b(s+[\W_]*h+[\W_]*[i1!]+[\W_]*t+)\b", 0.5, "profanity_explicit"),
    _tp("profanity", r"\b(b+[\W_]*[i1!]+[\W_]*t+[\W_]*c+[\W_]*h+)\b", 0.6, "profanity_explicit"),
    _tp("profanity", r"\b(a+[\W_]*s+[\W_]*s+)\b", 0.4, "profanity_mild"),
    _tp("profanity", r"\b(d+[\W_]*a+[\W_]*m+[\W_]*n+)\b", 0.3, "profanity_mild"),
]

THREAT_PATTERNS: list[ToxicityPattern] = [
    _tp("threat", r"\b(?:i(?:'ll|\s+will)\s+(?:kill|murder|destroy|end)\s+you)\b", 1.0, "direct_threat"),
    _tp("threat", r"\b(?:you(?:'ll|\s+will)\s+(?:die|regret|pay))\b", 0.9, "future_threat"),
    _tp("threat", r"\b(?:die|kys|kill\s+yourself)\b", 0.95, "self_harm_threat"),
    _tp("threat", r"\b(?:i\s+know\s+where\s+you\s+live)\b", 0.85, "stalking_threat"),
    _tp("threat", r"\b(?:gonna\s+(?:shoot|stab|beat))\b", 0.8, "violence_threat"),
]

HATE_SPEECH_PATTERNS: list[ToxicityPattern] = [
    _tp("hate_speech", r"\b(?:all\s+\w+\s+(?:are|should)\s+(?:die|burn|suffer))\b", 1.0, "genocidal_language"),
    _tp("hate_speech", r"\b(?:subhuman|vermin|cockroach|infestation)\b", 0.8, "dehumanization"),
    _tp("hate_speech", r"\b(?:go\s+back\s+(?:to|where)\s+your)\b", 0.7, "xenophobic"),
]

SEXUAL_PATTERNS: list[ToxicityPattern] = [
    _tp("sexual", r"\b(?:rape|molest|assault)\b", 0.9, "sexual_violence"),
    _tp("sexual", r"\b(?:child|minor)\s*(?:porn|sex|abuse)\b", 1.0, "csam_reference"),
]

SELF_HARM_PATTERNS: list[ToxicityPattern] = [
    _tp("self_harm", r"\b(?:suicide|suicidal|want\s+to\s+(?:die|kill\s+myself|end\s+(?:it|my\s+life)))\b", 0.8, "self_harm_reference"),
    _tp("self_harm", r"\b(?:cutting|slit\s+(?:my|your)\s+(?:wrists?|throat))\b", 0.85, "self_harm_method"),
]

VIOLENCE_PATTERNS: list[ToxicityPattern] = [
    _tp("violence", r"\b(?:make\s+a\s+(?:bomb|explosive))\b", 0.9, "weaponization"),
    _tp("violence", r"\b(?:shoot\s+(?:up|the))\b", 0.8, "mass_violence"),
]

HARASSMENT_PATTERNS: list[ToxicityPattern] = [
    _tp("harassment", r"\b(?:you\s+(?:are|'re)\s+(?:worthless|nothing|garbage|trash|scum))\b", 0.7, "degradation"),
    _tp("harassment", r"\b(?:nobody\s+(?:loves|cares|wants)\s+you)\b", 0.65, "isolation"),
]

ALL_TOXICITY_PATTERNS: list[ToxicityPattern] = (
    PROFANITY_PATTERNS + THREAT_PATTERNS + HATE_SPEECH_PATTERNS
    + SEXUAL_PATTERNS + SELF_HARM_PATTERNS + VIOLENCE_PATTERNS
    + HARASSMENT_PATTERNS
)

CATEGORY_SEVERITY: dict[ToxicityCategory, float] = {
    ToxicityCategory.PROFANITY: 0.3,
    ToxicityCategory.THREAT: 0.9,
    ToxicityCategory.HATE_SPEECH: 0.85,
    ToxicityCategory.SEXUAL: 0.95,
    ToxicityCategory.SELF_HARM: 0.9,
    ToxicityCategory.HARASSMENT: 0.6,
    ToxicityCategory.VIOLENCE: 0.85,
}


class ToxicityFilter(BaseFilter):
    """Score toxicity on a 0-1 scale with configurable threshold."""

    name = "toxicity"

    def __init__(
        self,
        *,
        enabled: bool = True,
        threshold: float = 0.5,
        blocked_categories: frozenset[ToxicityCategory] | None = None,
    ) -> None:
        super().__init__(enabled=enabled, threshold=threshold)
        self.blocked_categories = blocked_categories

    def _check(
        self, input_text: str, *, context: str = "", output: str = ""
    ) -> FilterResult:
        if not input_text.strip():
            return FilterResult(
                filter_name=self.name,
                decision=FilterDecision.ALLOW,
                score=0.0,
                risk_level=RiskLevel.LOW,
                details={"empty": True},
            )

        hits = self._scan(input_text)
        if not hits:
            return FilterResult(
                filter_name=self.name,
                decision=FilterDecision.ALLOW,
                score=0.0,
                risk_level=RiskLevel.LOW,
                details={"hit_count": 0},
            )

        score = self._aggregate_score(hits)
        categories_hit = list({h.category.value for h in hits})
        cat_details = self._category_breakdown(hits)
        blocked_cats = (
            [c.value for c in self.blocked_categories if c.value in categories_hit]
            if self.blocked_categories
            else []
        )

        blocked_by = []
        if score >= self.threshold:
            blocked_by = [h.label for h in hits if h.weight >= 0.8]
        if blocked_cats:
            blocked_by.extend([f"category:{c}" for c in blocked_cats])

        return FilterResult(
            filter_name=self.name,
            decision=self._decide(score),
            score=score,
            risk_level=self._score_to_risk(score),
            details={
                "hit_count": len(hits),
                "categories": categories_hit,
                "category_breakdown": cat_details,
                "max_weight": round(max(h.weight for h in hits), 3),
                "blocked_categories": blocked_cats,
            },
            blocked_by=blocked_by,
        )

    def _scan(self, text: str) -> list[ToxicityPattern]:
        return [p for p in ALL_TOXICITY_PATTERNS if p.regex.search(text)]

    def _aggregate_score(self, hits: list[ToxicityPattern]) -> float:
        max_weight = max(h.weight for h in hits)
        severity_boost = max(CATEGORY_SEVERITY.get(h.category, 0.5) for h in hits)
        unique_categories = len({h.category for h in hits})
        diversity_bonus = min(0.2, unique_categories * 0.05)
        score = (max_weight * 0.5) + (severity_boost * 0.35) + diversity_bonus
        return min(1.0, score)

    def _category_breakdown(self, hits: list[ToxicityPattern]) -> dict[str, float]:
        breakdown: dict[str, float] = {}
        for h in hits:
            key = h.category.value
            breakdown[key] = max(breakdown.get(key, 0.0), h.weight)
        return breakdown
