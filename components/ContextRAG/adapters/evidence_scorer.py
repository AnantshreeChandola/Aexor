"""
Intent-aware evidence relevance scoring.

Pure computation module -- no I/O, no LLM calls. Scores each
EvidenceItem by relevance to the current Intent using keyword
overlap, entity matching, type affinity, and confidence passthrough.

This module is consumed by BudgetManager to improve evidence
priority ordering before budget trimming.

Reference: LLD_context_optimization.md SS4
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from shared.schemas.evidence import EvidenceItem
from shared.schemas.intent import Intent

_STOP_WORDS: frozenset[str] = frozenset({
    "the", "a", "an", "to", "for", "with", "from", "in", "on", "at",
    "by", "of", "and", "or", "is", "my", "me", "i", "do", "get",
})

# Weight constants for scoring signals
_W_KEYWORD: float = 0.4
_W_ENTITY: float = 0.3
_W_AFFINITY: float = 0.2
_W_CONFIDENCE: float = 0.1

# Intent pattern -> relevant evidence types
_INTENT_EVIDENCE_AFFINITY: dict[str, list[str]] = {
    "email": ["contact", "preference"],
    "mail": ["contact", "preference"],
    "send": ["contact", "preference"],
    "reply": ["contact", "history"],
    "draft": ["contact", "preference"],
    "meeting": ["preference", "history", "contact"],
    "schedule": ["preference", "history", "contact"],
    "calendar": ["preference", "history"],
    "event": ["preference", "history", "contact"],
    "book": ["preference", "history", "contact"],
    "appointment": ["preference", "history", "contact"],
    "reschedule": ["preference", "history", "contact"],
    "availability": ["preference", "history"],
    "document": ["exemplar", "history", "preference"],
    "doc": ["exemplar", "history", "preference"],
    "create": ["preference", "exemplar"],
    "edit": ["history", "preference"],
    "task": ["history", "preference"],
    "todo": ["history", "preference"],
    "note": ["history", "preference"],
    "notion": ["history", "preference"],
}

MIN_EVIDENCE_ITEMS: int = 2
RELEVANCE_THRESHOLD: float = 0.15


@dataclass
class ScoredEvidence:
    """Evidence item with computed relevance score."""

    item: EvidenceItem
    relevance: float = 0.0
    match_reasons: list[str] = field(default_factory=list)


class EvidenceScorer:
    """Score evidence items by relevance to the current intent.

    Pure computation: no I/O, no LLM calls, no side effects.
    Thread-safe and stateless.
    """

    def score(
        self,
        intent: Intent,
        evidence: list[EvidenceItem],
    ) -> list[ScoredEvidence]:
        """Score each evidence item against the intent.

        Args:
            intent: Current user intent.
            evidence: Deduplicated evidence items from all sources.

        Returns:
            List of ScoredEvidence, sorted by relevance DESC.
        """
        if not evidence:
            return []

        intent_tokens = self._build_intent_tokens(intent)
        affinity_types = self._get_affinity_types(intent.intent)

        scored: list[ScoredEvidence] = []
        for item in evidence:
            se = self._score_item(item, intent, intent_tokens, affinity_types)
            scored.append(se)

        scored.sort(key=lambda s: -s.relevance)

        return scored

    def score_to_dict(
        self,
        intent: Intent,
        evidence: list[EvidenceItem],
    ) -> dict[str, float]:
        """Return a dict of evidence.key -> relevance score.

        Convenience method for BudgetManager integration.
        """
        scored = self.score(intent, evidence)
        return {s.item.key: s.relevance for s in scored}

    # ----- Internal scoring methods -----

    def _score_item(
        self,
        item: EvidenceItem,
        intent: Intent,
        intent_tokens: set[str],
        affinity_types: set[str],
    ) -> ScoredEvidence:
        """Compute weighted relevance score for a single evidence item."""
        reasons: list[str] = []

        # Signal 1: Keyword overlap
        evidence_tokens = self._build_evidence_tokens(item)
        overlap = intent_tokens & evidence_tokens
        keyword_score = len(overlap) / max(len(intent_tokens), 1)
        if overlap:
            reasons.append(f"keyword:{','.join(sorted(overlap))}")

        # Signal 2: Entity match
        entity_score = self._compute_entity_score(item, intent)
        if entity_score > 0:
            reasons.append(f"entity:{entity_score:.1f}")

        # Signal 3: Type affinity
        affinity_score = 1.0 if item.type in affinity_types else 0.0
        if affinity_score > 0:
            reasons.append(f"affinity:{item.type}")

        # Signal 4: Confidence passthrough
        confidence_score = item.confidence

        # Weighted sum
        relevance = (
            _W_KEYWORD * keyword_score
            + _W_ENTITY * entity_score
            + _W_AFFINITY * affinity_score
            + _W_CONFIDENCE * confidence_score
        )
        relevance = min(max(relevance, 0.0), 1.0)

        return ScoredEvidence(item=item, relevance=relevance, match_reasons=reasons)

    def _compute_entity_score(self, item: EvidenceItem, intent: Intent) -> float:
        """Check if evidence matches intent entities."""
        entities = intent.entities or {}
        # Direct value match: evidence.value appears in entity values
        for val in entities.values():
            if item.value == val:
                return 1.0
            if isinstance(val, str) and isinstance(item.value, str) and val.lower() == item.value.lower():
                return 1.0

        # Key name match: evidence.key matches or contains an entity key
        item_key_tokens = self._tokenize(item.key)
        for ent_key in entities:
            ent_tokens = self._tokenize(ent_key)
            if ent_tokens & item_key_tokens:
                return 0.8

        return 0.0

    def _build_intent_tokens(self, intent: Intent) -> set[str]:
        """Build token set from intent action and entities."""
        tokens = self._tokenize(intent.intent)
        for key in (intent.entities or {}):
            tokens |= self._tokenize(str(key))
        for val in (intent.entities or {}).values():
            if isinstance(val, str):
                tokens |= self._tokenize(val)
        return tokens

    def _build_evidence_tokens(self, item: EvidenceItem) -> set[str]:
        """Build token set from evidence key and value."""
        tokens = self._tokenize(item.key)
        if isinstance(item.value, str):
            tokens |= self._tokenize(item.value)
        return tokens

    def _get_affinity_types(self, intent_action: str) -> set[str]:
        """Look up which evidence types are relevant for this intent."""
        needle = intent_action.lower()
        types: set[str] = set()
        for keyword, evidence_types in _INTENT_EVIDENCE_AFFINITY.items():
            if keyword in needle:
                types.update(evidence_types)
        # If no match, all types are relevant (fail-open)
        if not types:
            types = {"preference", "history", "contact", "plan", "exemplar"}
        return types

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Split text into normalized tokens."""
        # Handle camelCase
        text = re.sub(r"([a-z])([A-Z])", r"\1_\2", text)
        # Split on non-alphanumeric
        parts = re.split(r"[^a-zA-Z0-9]+", text.lower())
        return {p for p in parts if p and len(p) > 1 and p not in _STOP_WORDS}
