"""Citation validator -- verify citations are present and well-formed.

Checks format, presence, and completeness of citations in LLM output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .base import BaseFilter, FilterDecision, FilterResult, RiskLevel


@dataclass(frozen=True, slots=True)
class Citation:
    raw: str
    citation_type: str
    start: int
    end: int


NUMERIC_CITATION = re.compile(r"\[(\d+)\]")
AUTHOR_YEAR_CITATION = re.compile(r"\((?:[A-Z][a-z]+(?:\s+(?:et\s+al\.?|&\s+[A-Z][a-z]+))?(?:,?\s*\d{4})?)\)")
FOOTNOTE_CITATION = re.compile(r"(?:\^(\d+))")
WIKI_STYLE_CITATION = re.compile(r"\[(?:[^\]]*?\]\([^)]+\))")
CLAIM_SENTENCE = re.compile(r"(?<=[.!?])\s+")
QUOTE_PATTERN = re.compile(r'"[^"]{20,}"' + r"|" + r"'[^']{20,}'")


@dataclass(slots=True)
class CitationStats:
    total_citations: int = 0
    numeric_citations: int = 0
    author_year_citations: int = 0
    footnote_citations: int = 0
    wiki_style_citations: int = 0
    claim_count: int = 0
    claims_with_citation: int = 0
    claims_with_quote: int = 0
    quote_count: int = 0

    @property
    def citation_density(self) -> float:
        return self.total_citations / max(self.claim_count, 1)

    @property
    def coverage_ratio(self) -> float:
        return self.claims_with_citation / max(self.claim_count, 1)

    @property
    def format_consistency(self) -> float:
        types = [self.numeric_citations, self.author_year_citations, self.footnote_citations, self.wiki_style_citations]
        active = [t for t in types if t > 0]
        if len(active) <= 1:
            return 1.0
        dominant = max(active)
        return dominant / sum(active)


class CitationValidator(BaseFilter):
    """Validate citation presence, format, and completeness in output."""

    name = "citation_validator"

    def __init__(
        self,
        *,
        enabled: bool = True,
        threshold: float = 0.5,
        require_citations: bool = True,
        min_citation_density: float = 0.3,
        require_format_consistency: bool = True,
    ) -> None:
        super().__init__(enabled=enabled, threshold=threshold)
        self.require_citations = require_citations
        self.min_citation_density = min_citation_density
        self.require_format_consistency = require_format_consistency

    def _check(
        self, input_text: str, *, context: str = "", output: str = ""
    ) -> FilterResult:
        if not output or not output.strip():
            return FilterResult(
                filter_name=self.name,
                decision=FilterDecision.ALLOW,
                score=0.0,
                risk_level=RiskLevel.LOW,
                details={"empty": True},
            )

        citations = self._extract_citations(output)
        stats = self._compute_stats(output, citations)
        score = self._compute_score(stats)

        return FilterResult(
            filter_name=self.name,
            decision=self._decide(score),
            score=score,
            risk_level=self._score_to_risk(score),
            details={
                "total_citations": stats.total_citations,
                "claim_count": stats.claim_count,
                "claims_with_citation": stats.claims_with_citation,
                "coverage_ratio": round(stats.coverage_ratio, 3),
                "citation_density": round(stats.citation_density, 3),
                "format_consistency": round(stats.format_consistency, 3),
                "citation_types": {
                    "numeric": stats.numeric_citations,
                    "author_year": stats.author_year_citations,
                    "footnote": stats.footnote_citations,
                    "wiki_style": stats.wiki_style_citations,
                },
                "quote_count": stats.quote_count,
            },
            blocked_by=self._blocked_reasons(stats, score),
        )

    def _extract_citations(self, text: str) -> list[Citation]:
        citations: list[Citation] = []
        for m in NUMERIC_CITATION.finditer(text):
            citations.append(Citation(m.group(), "numeric", m.start(), m.end()))
        for m in AUTHOR_YEAR_CITATION.finditer(text):
            citations.append(Citation(m.group(), "author_year", m.start(), m.end()))
        for m in FOOTNOTE_CITATION.finditer(text):
            citations.append(Citation(m.group(), "footnote", m.start(), m.end()))
        for m in WIKI_STYLE_CITATION.finditer(text):
            citations.append(Citation(m.group(), "wiki_style", m.start(), m.end()))
        return citations

    def _compute_stats(self, text: str, citations: list[Citation]) -> CitationStats:
        stats = CitationStats()
        stats.total_citations = len(citations)
        stats.numeric_citations = sum(1 for c in citations if c.citation_type == "numeric")
        stats.author_year_citations = sum(1 for c in citations if c.citation_type == "author_year")
        stats.footnote_citations = sum(1 for c in citations if c.citation_type == "footnote")
        stats.wiki_style_citations = sum(1 for c in citations if c.citation_type == "wiki_style")

        sentences = CLAIM_SENTENCE.split(text)
        citation_positions = sorted(c.start for c in citations)
        stats.quote_count = len(QUOTE_PATTERN.findall(text))

        for sentence in sentences:
            stripped = sentence.strip()
            if len(stripped.split()) < 5:
                continue
            stats.claim_count += 1
            sent_start = text.find(stripped)
            if sent_start < 0:
                continue
            sent_end = sent_start + len(stripped)
            if any(sent_start <= pos <= sent_end for pos in citation_positions):
                stats.claims_with_citation += 1

        return stats

    def _compute_score(self, stats: CitationStats) -> float:
        scores: list[float] = []

        if self.require_citations and stats.claim_count >= 2:
            coverage_score = 1.0 - stats.coverage_ratio
            scores.append(coverage_score * 0.4)

        if stats.claim_count >= 2:
            density_score = max(0.0, 1.0 - (stats.citation_density / self.min_citation_density))
            scores.append(density_score * 0.3)

        if self.require_format_consistency and stats.total_citations > 1:
            consistency_penalty = 1.0 - stats.format_consistency
            scores.append(consistency_penalty * 0.2)

        if stats.claim_count >= 3 and stats.total_citations == 0:
            scores.append(0.6)

        if not scores:
            return 0.0
        return min(1.0, sum(scores))

    def _blocked_reasons(self, stats: CitationStats, score: float) -> list[str]:
        reasons: list[str] = []
        if score < self.threshold:
            return reasons
        if stats.total_citations == 0 and stats.claim_count >= 2:
            reasons.append("no_citations_present")
        if stats.claim_count >= 2 and stats.coverage_ratio < 0.5:
            reasons.append("low_citation_coverage")
        if self.require_format_consistency and stats.format_consistency < 0.6 and stats.total_citations > 2:
            reasons.append("inconsistent_citation_format")
        return reasons
