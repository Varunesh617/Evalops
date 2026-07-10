"""PII detection filter -- SSN, email, phone, names, addresses.

Supports HIPAA-aware patterns and density-based scoring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .base import BaseFilter, FilterDecision, FilterResult, RiskLevel


@dataclass(frozen=True, slots=True)
class PIIPattern:
    name: str
    regex: re.Pattern[str]
    weight: float
    hipaa_category: str | None = None


def _pp(name: str, rx: str, weight: float, hipaa: str | None = None) -> PIIPattern:
    return PIIPattern(name, re.compile(rx), weight, hipaa)


# -- Standard PII patterns ---------------------------------------------------

SSN_PATTERN = _pp(
    "ssn", r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b", 1.0, "unique_identifier"
)
SSN_NO_DASH = _pp(
    "ssn_nodash", r"\b(?!000|666|9\d{2})\d{3}(?!00)\d{2}(?!0000)\d{4}\b", 0.7, "unique_identifier"
)
EMAIL_PATTERN = _pp(
    "email", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", 0.6, "electronic_pii"
)
US_PHONE = _pp(
    "us_phone", r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", 0.5, "electronic_pii"
)
INTERNATIONAL_PHONE = _pp(
    "intl_phone", r"\+\d{1,3}[-.\s]?\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b", 0.5, "electronic_pii"
)
CREDIT_CARD = _pp(
    "credit_card", r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b", 0.9, "financial"
)
US_PASSPORT = _pp(
    "passport", r"\b[A-Z]\d{8}\b", 0.8, "unique_identifier"
)
DOB_PATTERN = _pp(
    "dob", r"\b(?:0[1-9]|1[0-2])/(?:0[1-9]|[12]\d|3[01])/(?:19|20)\d{2}\b", 0.6, "demographic"
)
IP_ADDRESS = _pp(
    "ip_address", r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b", 0.4, "technical"
)
US_ADDRESS = _pp(
    "us_address", r"\d{1,5}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][a-zA-Z]+\s?){1,4}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Court|Ct|Lane|Ln|Way|Place|Pl)\b", 0.6, "demographic"
)
DEA_NUMBER = _pp(
    "dea_number", r"\b[ABCDEFGHJKLMNPRSTUVWXYZ][A-Z9]\d{7}\b", 0.9, "unique_identifier"
)
NPI_NUMBER = _pp(
    "npi_number", r"\b\d{10}\b", 0.3, "healthcare_identifier"
)

ALL_PATTERNS: list[PIIPattern] = [
    SSN_PATTERN, SSN_NO_DASH, EMAIL_PATTERN, US_PHONE, INTERNATIONAL_PHONE,
    CREDIT_CARD, US_PASSPORT, DOB_PATTERN, IP_ADDRESS, US_ADDRESS,
    DEA_NUMBER, NPI_NUMBER,
]

HIPAA_HEAVY: frozenset[str] = frozenset({
    "ssn", "ssn_nodash", "passport", "dea_number", "npi_number", "dob",
})


@dataclass(slots=True)
class PIIMatch:
    pattern_name: str
    matched_text: str
    start: int
    end: int
    weight: float
    hipaa_category: str | None


class PIIFilter(BaseFilter):
    """Detect PII in text with density-based scoring and HIPAA awareness."""

    name = "pii"

    def __init__(
        self,
        *,
        enabled: bool = True,
        threshold: float = 0.5,
        hipaa_mode: bool = False,
        redact_in_details: bool = True,
    ) -> None:
        super().__init__(enabled=enabled, threshold=threshold)
        self.hipaa_mode = hipaa_mode
        self.redact_in_details = redact_in_details
        self._patterns = self._select_patterns()

    def _select_patterns(self) -> list[PIIPattern]:
        if self.hipaa_mode:
            return ALL_PATTERNS
        return [p for p in ALL_PATTERNS if p.name not in ("npi_number", "dea_number")]

    def _check(
        self, input_text: str, *, context: str = "", output: str = ""
    ) -> FilterResult:
        matches = self._find_all(input_text)
        if not matches:
            return FilterResult(
                filter_name=self.name,
                decision=FilterDecision.ALLOW,
                score=0.0,
                risk_level=RiskLevel.LOW,
                details={"match_count": 0, "categories": []},
            )

        score = self._calculate_density_score(input_text, matches)
        hipaa_flags = [m.pattern_name for m in matches if m.pattern_name in HIPAA_HEAVY and m.hipaa_category]
        categories = list({m.hipaa_category for m in matches if m.hipaa_category})

        details: dict[str, object] = {
            "match_count": len(matches),
            "categories": categories,
            "hipaa_flags": hipaa_flags,
            "unique_types": list({m.pattern_name for m in matches}),
        }
        if self.redact_in_details:
            details["matches"] = [
                {"type": m.pattern_name, "span": [m.start, m.end]}
                for m in matches
            ]
        else:
            details["matches"] = [
                {"type": m.pattern_name, "text": m.matched_text, "span": [m.start, m.end]}
                for m in matches
            ]

        if self.hipaa_mode and hipaa_flags:
            score = min(1.0, score + 0.2)

        blocked_by = [m.pattern_name for m in matches if m.weight >= 0.8] if score >= self.threshold else []

        return FilterResult(
            filter_name=self.name,
            decision=self._decide(score),
            score=score,
            risk_level=self._score_to_risk(score),
            details=details,
            blocked_by=blocked_by,
        )

    def _find_all(self, text: str) -> list[PIIMatch]:
        matches: list[PIIMatch] = []
        seen_spans: set[tuple[int, int]] = set()

        for pattern in self._patterns:
            for m in pattern.regex.finditer(text):
                span = (m.start(), m.end())
                if span in seen_spans:
                    continue
                seen_spans.add(span)
                matches.append(PIIMatch(
                    pattern_name=pattern.name,
                    matched_text=m.group(),
                    start=m.start(),
                    end=m.end(),
                    weight=pattern.weight,
                    hipaa_category=pattern.hipaa_category,
                ))

        matches.sort(key=lambda x: x.start)
        return matches

    def _calculate_density_score(self, text: str, matches: list[PIIMatch]) -> float:
        if not text:
            return 0.0
        char_coverage = sum(m.end - m.start for m in matches) / len(text)
        type_diversity = len({m.pattern_name for m in matches}) / max(len(self._patterns), 1)
        weight_sum = sum(m.weight for m in matches)
        normalized_weight = min(1.0, weight_sum / len(matches)) if matches else 0.0
        score = (normalized_weight * 0.5) + (min(1.0, char_coverage * 10) * 0.25) + (type_diversity * 0.25)
        return min(1.0, score)
