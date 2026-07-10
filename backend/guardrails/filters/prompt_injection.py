"""Prompt-injection detection filter.

Uses a layered approach:
1. Literal keyword/pattern scan (fast, high-recall).
2. Structural heuristics -- role-reset markers, instruction overrides.
3. Obfuscation-aware scanning -- leetspeak, zero-width chars, Unicode tricks.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .base import BaseFilter, FilterDecision, FilterResult, RiskLevel


@dataclass(frozen=True, slots=True)
class InjectionPattern:
    pattern: re.Pattern[str]
    weight: float
    description: str


def _p(rx: str, w: float, d: str) -> InjectionPattern:
    return InjectionPattern(re.compile(rx, re.I), w, d)


# -- Pattern banks -----------------------------------------------------------

HIGH_RISK_PATTERNS: list[InjectionPattern] = [
    _p(r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)", 1.0, "explicit instruction override"),
    _p(r"you\s+are\s+now\s+(?:a|an)\s+", 0.95, "role reassignment"),
    _p(r"system\s*:\s*|<\|system\|>|<\|im_start\|>system", 1.0, "system prompt delimiter injection"),
    _p(r"jailbreak|DAN\s+mode|do\s+anything\s+now", 1.0, "jailbreak keyword"),
    _p(r"(?:forget|disregard|override)\s+(?:all\s+)?(?:your|the)\s+(?:rules?|constraints?|guidelines?)", 0.95, "constraint override"),
    _p(r"pretend\s+(?:you(?:'re|\s+are)|that)\s+(?:a\s+)?(?:different|new|evil|unrestricted)", 0.9, "persona manipulation"),
]

MEDIUM_RISK_PATTERNS: list[InjectionPattern] = [
    _p(r"(?:repeat|echo|output|print|return)\s+(?:the\s+)?(?:above|previous|earlier|full)\s+(?:text|prompt|instructions?)", 0.7, "prompt exfiltration attempt"),
    _p(r"translate\s+(?:this|the\s+following)\s+(?:to|into)\s+(?:code|python|sql|json)", 0.5, "code extraction probe"),
    _p(r"what\s+(?:are|is)\s+your\s+(?:system\s+)?(?:prompt|instructions?|rules?)", 0.6, "system prompt extraction"),
    _p(r"\b(?:DAN|STAN|KEVIN)\s*:\s*", 0.85, "jailbreak persona delimiter"),
    _p(r"(?:bypass|override|disable)\s+(?:all\s+)?(?:safety|security|content)\s+(?:filters?|checks?|guards?)", 0.8, "safety filter bypass"),
]

LOW_RISK_PATTERNS: list[InjectionPattern] = [
    _p(r"(?:please|kindly)\s+(?:bypass|skip|disable|turn\s+off)", 0.3, "soft bypass request"),
    _p(r"(?:hypothetically|theoretically|in\s+a\s+fiction(?:al)?)\s*,?\s*(?:if|what\s+if|suppose)", 0.2, "hypothetical framing"),
    _p(r"(?:roleplay|role-play|act\s+as\s+if)\s+(?:you\s+are|you're)\s+(?:not\s+bound)", 0.35, "roleplay constraint release"),
]

ROLE_RESET_MARKERS: list[str] = [
    "<|im_start|>",
    "<|assistant|>",
    "[INST]",
    "<<SYS>>",
    "Human:",
    "Assistant:",
    "System:",
]


class PromptInjectionFilter(BaseFilter):
    """Detect prompt-injection attempts in user input."""

    name = "prompt_injection"

    def __init__(
        self,
        *,
        enabled: bool = True,
        threshold: float = 0.6,
        strip_obfuscation: bool = True,
    ) -> None:
        super().__init__(enabled=enabled, threshold=threshold)
        self.strip_obfuscation = strip_obfuscation

    def _check(
        self, input_text: str, *, context: str = "", output: str = ""
    ) -> FilterResult:
        normalized = self._normalize(input_text)
        score, matches = self._scan_patterns(normalized)
        role_score = self._check_role_resets(normalized)
        combo_score = self._check_combo_signals(normalized)

        final_score = min(1.0, max(score, role_score, combo_score))
        blocked_by = [m.description for m in matches] if final_score >= self.threshold else []

        return FilterResult(
            filter_name=self.name,
            decision=self._decide(final_score),
            score=final_score,
            risk_level=self._score_to_risk(final_score),
            details={
                "pattern_matches": len(matches),
                "role_reset_hits": role_score > 0,
                "combo_signal": combo_score > 0,
                "match_descriptions": [m.description for m in matches],
                "normalized_length": len(normalized),
            },
            blocked_by=blocked_by,
        )

    def _normalize(self, text: str) -> str:
        if not self.strip_obfuscation:
            return text
        cleaned = self._remove_zero_width(text)
        cleaned = self._decode_leetspeak(cleaned)
        cleaned = self._normalize_unicode(cleaned)
        return cleaned

    def _scan_patterns(self, text: str) -> tuple[float, list[InjectionPattern]]:
        all_patterns = (
            [(p, "high") for p in HIGH_RISK_PATTERNS]
            + [(p, "medium") for p in MEDIUM_RISK_PATTERNS]
            + [(p, "low") for p in LOW_RISK_PATTERNS]
        )
        hits: list[InjectionPattern] = []
        for pattern, _tier in all_patterns:
            if pattern.pattern.search(text):
                hits.append(pattern)
        if not hits:
            return 0.0, hits
        best_weight = max(h.weight for h in hits)
        density_bonus = min(0.15, len(hits) * 0.05)
        score = min(1.0, best_weight + density_bonus)
        return score, hits

    def _check_role_resets(self, text: str) -> float:
        count = sum(1 for marker in ROLE_RESET_MARKERS if marker.lower() in text.lower())
        if count == 0:
            return 0.0
        if count == 1:
            return 0.4
        return min(1.0, 0.5 + count * 0.15)

    def _check_combo_signals(self, text: str) -> float:
        has_imperative = bool(re.search(r"\b(?:do|now|always|never)\b", text, re.I))
        has_negation = bool(re.search(r"\b(?:not|no|without|don't|do not)\b", text, re.I))
        has_directive = bool(re.search(r"\b(?:make|write|generate|create|output)\b", text, re.I))
        signals = sum([has_imperative, has_negation, has_directive])
        if signals >= 3:
            return 0.45
        if signals == 2:
            return 0.25
        return 0.0

    @staticmethod
    def _remove_zero_width(text: str) -> str:
        zero_width = "\u200b\u200c\u200d\ufeff\u00ad\u2060\u180e"
        return "".join(ch for ch in text if ch not in zero_width)

    @staticmethod
    def _decode_leetspeak(text: str) -> str:
        replacements = {
            "0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
            "7": "t", "@": "a", "$": "s", "!": "i", "+": "t",
        }
        result: list[str] = []
        for ch in text:
            result.append(replacements.get(ch, ch))
        return "".join(result)

    @staticmethod
    def _normalize_unicode(text: str) -> str:
        return unicodedata.normalize("NFKD", text)
