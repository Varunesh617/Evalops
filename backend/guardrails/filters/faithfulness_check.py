"""Faithfulness guardrail -- check if output is faithful to context.

Uses claim extraction and support scoring to determine hallucination risk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .base import BaseFilter, FilterDecision, FilterResult, RiskLevel


@dataclass(frozen=True, slots=True)
class Claim:
    text: str
    start: int
    end: int
    supported: bool = False
    support_score: float = 0.0


SENTENCE_SPLITTER = re.compile(r"(?<=[.!?])\s+")
HEADCHECK_WORDS = re.compile(
    r"\b(?:according to|the (?:document|text|context|report) (?:states?|says?|mentions?)|"
    r"based on|as shown in|as described|the evidence)\b",
    re.I,
)
CONTRADICTION_MARKERS = re.compile(
    r"\b(?:however|on the contrary|in contrast|nevertheless|despite this|"
    r"this contradicts|this disagrees|actually|in fact|contrary to)\b",
    re.I,
)
HEDGE_WORDS = re.compile(
    r"\b(?:perhaps|maybe|possibly|might|could|it is (?:possible|likely|unlikely)|"
    r"probably|seems?|appears? to be|it is believed)\b",
    re.I,
)
QUANTIFIED_STATEMENT = re.compile(
    r"\b(?:\d+(?:\.\d+)?\s*(?:%|percent|times|fold|million|billion|thousand))\b", re.I
)
CITATION_REF = re.compile(r"\[(?:\d+|[a-zA-Z]+)\]|\(\w+\s+et\s+al\.?,?\s*\d{4}\)")


class FaithfulnessFilter(BaseFilter):
    """Guardrail that blocks outputs unfaithful to provided context."""

    name = "faithfulness"

    def __init__(
        self,
        *,
        enabled: bool = True,
        threshold: float = 0.5,
        min_context_length: int = 20,
    ) -> None:
        super().__init__(enabled=enabled, threshold=threshold)
        self.min_context_length = min_context_length

    def _check(
        self, input_text: str, *, context: str = "", output: str = ""
    ) -> FilterResult:
        if not output or not context:
            return FilterResult(
                filter_name=self.name,
                decision=FilterDecision.ALLOW,
                score=0.0,
                risk_level=RiskLevel.LOW,
                details={"skipped": True, "reason": "no output or context provided"},
            )

        if len(context.strip()) < self.min_context_length:
            return FilterResult(
                filter_name=self.name,
                decision=FilterDecision.ALLOW,
                score=0.0,
                risk_level=RiskLevel.LOW,
                details={"skipped": True, "reason": "context too short"},
            )

        claims = self._extract_claims(output)
        if not claims:
            return FilterResult(
                filter_name=self.name,
                decision=FilterDecision.ALLOW,
                score=0.0,
                risk_level=RiskLevel.LOW,
                details={"claim_count": 0},
            )

        scored_claims = [self._score_claim(c, output, context) for c in claims]
        faithfulness_score = self._compute_score(scored_claims)
        unsupported = [c for c in scored_claims if not c.supported]

        return FilterResult(
            filter_name=self.name,
            decision=self._decide(faithfulness_score),
            score=faithfulness_score,
            risk_level=self._score_to_risk(faithfulness_score),
            details={
                "claim_count": len(scored_claims),
                "supported_count": len(scored_claims) - len(unsupported),
                "unsupported_count": len(unsupported),
                "avg_support": round(
                    sum(c.support_score for c in scored_claims) / len(scored_claims), 3
                ) if scored_claims else 0.0,
                "unsupported_texts": [c.text[:120] for c in unsupported[:5]],
                "has_hedging": bool(HEDGE_WORDS.search(output)),
                "has_contradiction_markers": bool(CONTRADICTION_MARKERS.search(output)),
            },
            blocked_by=["unfaithful_claims"] if faithfulness_score >= self.threshold else [],
        )

    def _extract_claims(self, text: str) -> list[Claim]:
        sentences = SENTENCE_SPLITTER.split(text)
        claims: list[Claim] = []
        offset = 0
        for sentence in sentences:
            stripped = sentence.strip()
            if len(stripped) < 10:
                offset += len(sentence) + 1
                continue
            if self._is_assertive(stripped):
                claims.append(Claim(text=stripped, start=offset, end=offset + len(stripped)))
            offset += len(sentence) + 1
        return claims

    def _is_assertive(self, sentence: str) -> bool:
        if HEDGE_WORDS.search(sentence):
            return True
        if QUANTIFIED_STATEMENT.search(sentence):
            return True
        if sentence[0].isupper() and len(sentence.split()) >= 4:
            return True
        return False

    def _score_claim(self, claim: Claim, full_output: str, context: str) -> Claim:
        support = self._token_overlap_support(claim.text, context)
        if support < 0.3:
            bigram_support = self._bigram_support(claim.text, context)
            support = max(support, bigram_support)
        if HEADCHECK_WORDS.search(claim.text):
            support = min(1.0, support + 0.1)
        if CONTRADICTION_MARKERS.search(claim.text):
            support = max(0.0, support - 0.15)
        return Claim(
            text=claim.text,
            start=claim.start,
            end=claim.end,
            supported=support >= 0.3,
            support_score=round(min(1.0, support), 3),
        )

    @staticmethod
    def _token_overlap_support(claim: str, context: str) -> float:
        claim_tokens = set(claim.lower().split())
        context_tokens = set(context.lower().split())
        stopwords = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to", "for", "of", "and", "or", "that", "this", "with", "as", "by"}
        claim_content = claim_tokens - stopwords
        context_content = context_tokens - stopwords
        if not claim_content:
            return 0.0
        overlap = claim_content & context_content
        return len(overlap) / len(claim_content)

    @staticmethod
    def _bigram_support(claim: str, context: str) -> float:
        claim_lower = claim.lower()
        context_lower = context.lower()
        words_c = claim_lower.split()
        words_x = context_lower.split()
        if len(words_c) < 2:
            return 0.0
        claim_bigrams = {f"{words_c[i]} {words_c[i+1]}" for i in range(len(words_c) - 1)}
        context_bigrams = {f"{words_x[i]} {words_x[i+1]}" for i in range(len(words_x) - 1)}
        if not claim_bigrams:
            return 0.0
        overlap = claim_bigrams & context_bigrams
        return len(overlap) / len(claim_bigrams)

    @staticmethod
    def _compute_score(claims: list[Claim]) -> float:
        if not claims:
            return 0.0
        unsupported_weight = sum(1.0 - c.support_score for c in claims if not c.supported)
        total_weight = len(claims)
        return min(1.0, unsupported_weight / total_weight) if total_weight else 0.0
