# Context Optimization -- Low-Level Design (LLD)

**Component**: `components/Planner/` (with changes in `components/ContextRAG/`)
**Layer**: Domain / Service Layer
**Type**: Cross-component optimization (Planner + ContextRAG)
**Created**: 2026-04-16
**Branch**: `feat/planner-context-optimization`
**Parent LLDs**: `components/Planner/LLD.md`, `components/ContextRAG/LLD.md`

---

## 1. Purpose & Scope

Reduce LLM call latency and token cost in the Planner by narrowing the two largest input token contributors: the evidence list and the tool catalog. The current system sends all gathered evidence and provider-filtered tools to the LLM, resulting in 20-24 second plan generation calls.

**Problem Statement** (from production logs):
- 226 total MCP tools filtered to 31 by provider -- still too many for a focused prompt
- 9 evidence items sent to LLM -- many irrelevant to the specific intent
- LLM call takes 20-24 seconds (input token bound)

**Two improvements**:
- **Improvement A: Smarter Context Assembly** -- Build an intent-aware evidence scorer in ContextRAG that ranks and prunes evidence to only the 2-3 items relevant to the specific intent, instead of sending everything under the byte budget.
- **Improvement B: Tool Catalog Pruning** -- Add tool-level filtering within a provider (not just provider-level) so that "send an email" sees 3-5 Gmail tools instead of all 31.

**Target outcome**:
- Evidence items sent to LLM: 9 down to 2-4 (55-75% reduction)
- Tools sent to LLM: 31 down to 3-8 (75-90% reduction)
- Estimated input token reduction: 60-80%
- Estimated LLM latency improvement: 20-24s down to 5-10s

**Out of scope**:
- Output token optimization (already compact via JSON-only output)
- LLM model changes (handled by existing fallback hierarchy)
- Prompt template changes beyond evidence/tool sections
- Caching LLM responses (different optimization axis)

---

## 2. Conformance

| Document | Version | Reference |
|----------|---------|-----------|
| GLOBAL_SPEC.md | v3.1 | Section 2.2 Evidence Item, Section 7 Context Policy |
| Planner LLD.md | current | Section 6.4 Prompt Builder, Section 7.1 generate_plan() |
| ContextRAG LLD.md | current | Section 4.1 Service Interface, Section 6.6 Budget Manager |
| MODULAR_ARCHITECTURE.md | v2.0 | Section 1 Layered Architecture (ContextRAG, Planner) |
| ADR-0001 | Accepted | Component-first folder layout |

---

## 3. Architecture Overview

### 3.1 Current Flow (before optimization)

```
Intent
  |
  v
ContextRAG.gather_evidence(intent)
  |-- ProfileStore: get_all_preferences()      --> ~5-8 evidence items
  |-- History: get_facts_by_intent()           --> ~3-5 evidence items
  |-- PlanLibrary: get_plans_by_intent()       --> ~2-3 evidence items
  |-- VectorIndex: search()                    --> ~1-3 evidence items
  |
  v  deduplicate + budget trim (2048 bytes)
  ~9 evidence items
  |
  v
ToolCatalog.get_user_tools()
  |-- 226 tools total
  |-- filter_tools_by_intent() (provider level) --> 31 tools
  |-- compact_tool_schemas()                    --> 31 compacted tools
  |
  v
PromptBuilder.build_user_prompt(intent, 9 evidence, 31 tools)
  |
  v  [large prompt, high input tokens]
LLM call: 20-24 seconds
```

### 3.2 Optimized Flow (after changes)

```
Intent
  |
  v
ContextRAG.gather_evidence(intent)
  |-- [same 4 sources, concurrent fetch]
  |-- deduplicate
  |-- NEW: EvidenceScorer.score(intent, evidence) --> relevance scores
  |-- budget trim (2048 bytes, but relevance-sorted instead of tier-only)
  |
  v  2-4 highly relevant evidence items
  |
  v
ToolCatalog.get_user_tools()
  |-- 226 tools total
  |-- filter_tools_by_intent() (provider level) --> 31 tools
  |-- NEW: filter_tools_by_action() (tool level) --> 3-8 tools
  |-- compact_tool_schemas()                     --> 3-8 compacted tools
  |
  v
PromptBuilder.build_user_prompt(intent, 3 evidence, 5 tools)
  |
  v  [much smaller prompt, fewer input tokens]
LLM call: 5-10 seconds (estimated)
```

### 3.3 Blast Radius Analysis

| Change | Failure Mode | Impact | Mitigation |
|--------|-------------|--------|------------|
| EvidenceScorer returns empty scores | All evidence gets score=0 | Falls back to tier+confidence sorting (current behavior) | Default score = 0.0; sorting is stable |
| EvidenceScorer is too aggressive | Important evidence pruned | LLM generates a worse plan | Min evidence floor = 2 items; fail-open design |
| Tool action filter removes needed tools | Plan cannot reference required tool | Falls back to full provider tool set | Fail-open: if filter returns 0 tools, use unfiltered set |
| Scoring adds latency | gather_evidence() exceeds 150ms p95 | Scoring is pure CPU, no I/O; budgeted at < 5ms | Scoring is O(n * m) where n=evidence, m=keywords; both small |

### 3.4 Design Principles

1. **Fail-open**: Both filters return the unfiltered set if they would produce an empty result
2. **No new external calls**: All filtering/scoring is pure computation over already-fetched data
3. **Additive changes**: New modules are added alongside existing code; existing behavior is default fallback
4. **Deterministic**: Same intent + same evidence = same scored output (no randomness, no LLM calls)
5. **Measurable**: Before/after metrics on token count, latency, and plan quality

---

## 4. Improvement A: Smarter Context Assembly

### 4.1 Intent-to-Evidence Relevance Scoring

The core insight: not all evidence is equally relevant to every intent. When the user says "send an email to Alice," the evidence item `meeting_duration_min=30` is irrelevant, but `alice_email=alice@company.com` is critical.

#### 4.1.1 Relevance Scoring Algorithm

The `EvidenceScorer` uses a multi-signal approach to assign a relevance score (0.0 to 1.0) to each evidence item relative to the current intent:

```python
@dataclass
class ScoredEvidence:
    """Evidence item with computed relevance score."""
    item: EvidenceItem
    relevance: float  # 0.0 to 1.0
    match_reasons: list[str]  # for debugging/logging


class EvidenceScorer:
    """Score evidence items by relevance to the current intent.

    Pure computation -- no I/O, no LLM calls, no side effects.
    Scoring signals (weighted sum, clamped to [0.0, 1.0]):

    1. Key-intent keyword overlap (weight: 0.4)
       - Tokenize intent.intent and intent.entities keys/values
       - Tokenize evidence.key
       - Score = |intersection| / |intent_tokens|

    2. Entity value match (weight: 0.3)
       - If evidence.value appears as a value in intent.entities, score = 1.0
       - If evidence.key matches an entity key name, score = 0.8

    3. Type-intent affinity (weight: 0.2)
       - Static mapping: intent patterns -> preferred evidence types
       - e.g., "send_email" -> ["contact", "preference"]
       - e.g., "schedule_meeting" -> ["preference", "history", "contact"]

    4. Confidence passthrough (weight: 0.1)
       - Original evidence.confidence passed through
    """
```

#### 4.1.2 Intent-Type Affinity Map

Static mapping from intent action patterns to evidence types that are typically relevant:

```python
_INTENT_EVIDENCE_AFFINITY: dict[str, list[str]] = {
    # Email intents need contacts and email preferences
    "email": ["contact", "preference"],
    "mail": ["contact", "preference"],
    "send": ["contact", "preference"],
    "reply": ["contact", "history"],
    "draft": ["contact", "preference"],

    # Calendar/meeting intents need preferences, history, contacts
    "meeting": ["preference", "history", "contact"],
    "schedule": ["preference", "history", "contact"],
    "calendar": ["preference", "history"],
    "event": ["preference", "history", "contact"],
    "book": ["preference", "history", "contact"],
    "appointment": ["preference", "history", "contact"],
    "reschedule": ["preference", "history", "contact"],
    "availability": ["preference", "history"],

    # Document intents need exemplars and history
    "document": ["exemplar", "history", "preference"],
    "doc": ["exemplar", "history", "preference"],
    "create": ["preference", "exemplar"],
    "edit": ["history", "preference"],

    # Task/note intents
    "task": ["history", "preference"],
    "todo": ["history", "preference"],
    "note": ["history", "preference"],
    "notion": ["history", "preference"],

    # Default: all types equally relevant
}
```

#### 4.1.3 Key-Intent Keyword Matching

Tokenization strategy for keyword overlap:

```python
def _tokenize(text: str) -> set[str]:
    """Split text into normalized tokens for matching.

    Handles:
    - snake_case: "schedule_meeting" -> {"schedule", "meeting"}
    - camelCase: "scheduleMeeting" -> {"schedule", "meeting"}
    - Dotted: "google.calendar" -> {"google", "calendar"}
    - Strips common stop words: {"the", "a", "an", "to", "for", "with", "from"}
    """
```

The keyword overlap is computed between:
- **Intent tokens**: `_tokenize(intent.intent)` UNION `_tokenize(str(k))` for k in `intent.entities.keys()` UNION entity values that are strings
- **Evidence tokens**: `_tokenize(evidence.key)` UNION `_tokenize(str(evidence.value))` if value is a string

Score = `|intent_tokens INTERSECTION evidence_tokens| / max(|intent_tokens|, 1)`

#### 4.1.4 Integration into ContextRAG

The `EvidenceScorer` is integrated into the existing `BudgetManager` as an optional scoring step. The `BudgetManager.enforce_budget()` method currently sorts by `(tier ASC, confidence DESC)`. With the scorer, the sort key becomes `(relevance DESC, tier ASC, confidence DESC)`:

```python
# In BudgetManager (modified):
def enforce_budget(
    self,
    evidence: list[EvidenceItem],
    relevance_scores: dict[str, float] | None = None,
) -> tuple[list[EvidenceItem], int]:
    """Sort by priority, trim to budget.

    If relevance_scores is provided (key=evidence.key -> float),
    sort by: (relevance DESC, tier ASC, confidence DESC).
    Otherwise, fall back to existing sort: (tier ASC, confidence DESC).
    """
```

This ensures backward compatibility -- if no scorer is wired, behavior is unchanged.

#### 4.1.5 Minimum Evidence Floor

To prevent over-pruning, the scorer enforces a minimum of 2 evidence items regardless of score. The floor includes:
1. The highest-scoring item (always kept)
2. At least one preference-type item if available (ensures user preferences are always represented)

### 4.2 New File: `components/ContextRAG/adapters/evidence_scorer.py`

```python
"""
Intent-aware evidence relevance scoring.

Pure computation module -- no I/O, no LLM calls. Scores each
EvidenceItem by relevance to the current Intent using keyword
overlap, entity matching, type affinity, and confidence passthrough.

This module is consumed by BudgetManager to improve evidence
priority ordering before budget trimming.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

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
            Always returns at least min(len(evidence), MIN_EVIDENCE_ITEMS)
            items with relevance > 0.
        """
        if not evidence:
            return []

        intent_tokens = self._build_intent_tokens(intent)
        affinity_types = self._get_affinity_types(intent.intent)

        scored: list[ScoredEvidence] = []
        for item in evidence:
            se = self._score_item(item, intent, intent_tokens, affinity_types)
            scored.append(se)

        # Sort by relevance descending
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
            if isinstance(val, str) and isinstance(item.value, str):
                if val.lower() == item.value.lower():
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
```

### 4.3 Modified File: `components/ContextRAG/adapters/budget_manager.py`

Changes to `enforce_budget()` to accept optional relevance scores:

```python
# New signature:
def enforce_budget(
    self,
    evidence: list[EvidenceItem],
    relevance_scores: dict[str, float] | None = None,
) -> tuple[list[EvidenceItem], int]:
    """Sort by priority, trim to budget.

    When relevance_scores is provided:
      Sort key: (relevance DESC, tier ASC, confidence DESC)
    When relevance_scores is None (backward compat):
      Sort key: (tier ASC, confidence DESC)

    Always returns at least MIN_EVIDENCE_ITEMS items if available,
    regardless of budget (to prevent over-pruning).
    """
```

The existing default behavior (no relevance scores) is preserved exactly. The new scoring path is only activated when the ContextRAG service passes scores.

### 4.4 Modified File: `components/ContextRAG/service/context_rag_service.py`

Changes to `gather_evidence()` to integrate the scorer:

```python
# In __init__, add optional scorer:
def __init__(
    self,
    preference_service: Any,
    fact_service: Any,
    pattern_service: Any,
    plan_service: Any,
    vector_index_service: Any | None,
    evidence_scorer: EvidenceScorer | None = None,  # NEW
) -> None:
    ...
    self._evidence_scorer = evidence_scorer or EvidenceScorer()

# In gather_evidence(), after deduplication (step 5), before budget trim (step 6):

    # 5b. Score evidence by relevance to intent (NEW)
    relevance_scores: dict[str, float] | None = None
    if self._evidence_scorer is not None:
        relevance_scores = self._evidence_scorer.score_to_dict(intent, all_evidence)

    # 6. Budget enforcement (sort + trim) -- now with relevance scores
    trimmed, total_bytes = self._budget_manager.enforce_budget(
        all_evidence, relevance_scores=relevance_scores,
    )
```

---

## 5. Improvement B: Tool Catalog Pruning

### 5.1 Action-Level Tool Filtering

The current `filter_tools_by_intent()` maps keywords to provider names (e.g., "email" -> "gmail"), which reduces 226 tools to ~31. This is still too many because a single provider may have dozens of actions.

The new `filter_tools_by_action()` adds a second filtering pass that matches intent keywords against individual tool names and descriptions, keeping only tools whose action is plausibly relevant.

#### 5.1.1 Action Keyword Matching Algorithm

```python
def filter_tools_by_action(
    tools: list[Any],
    intent_type: str | None,
    intent_entities: dict[str, Any] | None = None,
) -> list[Any]:
    """Narrow a provider-filtered tool list to action-relevant tools.

    Two-pass scoring:
    1. Direct action keyword match: tokenize intent and tool name/description,
       score by overlap
    2. Entity hint match: if intent has entities that hint at operations
       (e.g., "email" entity -> SEND_EMAIL, "event_id" -> UPDATE/DELETE)

    Args:
        tools: Provider-filtered tools (already narrowed by provider).
        intent_type: Free-form intent string.
        intent_entities: Entities from the intent (optional).

    Returns:
        Filtered list. If filter would return 0 tools, returns input unchanged
        (fail-open). Caps at MAX_TOOLS_PER_INTENT tools.
    """
```

#### 5.1.2 Intent-to-Action Map

For high-precision matching, a static map of intent patterns to action keywords:

```python
_INTENT_ACTION_MAP: dict[str, tuple[str, ...]] = {
    # Email actions
    "send_email": ("SEND_EMAIL", "CREATE_DRAFT"),
    "draft_email": ("CREATE_DRAFT", "SEND_EMAIL"),
    "reply_email": ("REPLY_TO_EMAIL", "SEND_EMAIL", "GET_EMAIL"),
    "forward_email": ("SEND_EMAIL", "GET_EMAIL"),
    "read_email": ("GET_EMAIL", "LIST_EMAILS", "FETCH_EMAILS"),
    "list_email": ("LIST_EMAILS", "FETCH_EMAILS"),
    "search_email": ("LIST_EMAILS", "FETCH_EMAILS"),

    # Calendar actions
    "schedule_meeting": ("CREATE_EVENT", "FIND_FREE_SLOTS", "FIND_EVENT", "LIST_EVENTS", "LIST_ALL_CALENDARS"),
    "book_meeting": ("CREATE_EVENT", "FIND_FREE_SLOTS", "FIND_EVENT", "LIST_EVENTS", "LIST_ALL_CALENDARS"),
    "create_event": ("CREATE_EVENT", "FIND_FREE_SLOTS", "FIND_EVENT"),
    "create_meeting": ("CREATE_EVENT", "FIND_FREE_SLOTS", "FIND_EVENT", "LIST_EVENTS"),
    "list_meetings": ("FIND_EVENT", "LIST_EVENTS", "LIST_ALL_CALENDARS", "EVENTS_LIST_ALL_CALENDARS"),
    "check_calendar": ("FIND_EVENT", "LIST_EVENTS", "LIST_ALL_CALENDARS", "EVENTS_LIST_ALL_CALENDARS"),
    "reschedule": ("UPDATE_EVENT", "FIND_EVENT", "FIND_FREE_SLOTS", "LIST_EVENTS"),
    "cancel_meeting": ("DELETE_EVENT", "FIND_EVENT", "LIST_EVENTS"),
    "cancel_event": ("DELETE_EVENT", "FIND_EVENT"),
    "availability": ("FIND_FREE_SLOTS", "FIND_EVENT", "LIST_EVENTS"),
    "freebusy": ("FIND_FREE_SLOTS",),

    # Docs/Drive actions
    "create_document": ("CREATE_DOCUMENT", "CREATE_DOCUMENT_FROM_TEXT"),
    "edit_document": ("APPEND_TEXT", "UPDATE_DOCUMENT"),
    "upload_file": ("UPLOAD_FILE",),
    "download_file": ("DOWNLOAD_FILE",),
    "search_drive": ("SEARCH_FILE", "FIND_FILE", "LIST_FILES"),
    "list_files": ("LIST_FILES", "SEARCH_FILE"),

    # Notion actions
    "create_page": ("CREATE_PAGE", "CREATE_A_NEW_PAGE"),
    "create_task": ("CREATE_PAGE", "CREATE_A_NEW_PAGE", "CREATE_BLOCK"),
    "list_tasks": ("FETCH_PAGE", "LIST_PAGES", "FETCH_DATABASE"),
    "search_notion": ("SEARCH_NOTION", "FETCH_PAGE"),

    # GitHub actions
    "create_issue": ("CREATE_ISSUE", "ISSUES_CREATE"),
    "list_issues": ("ISSUES_LIST", "LIST_ISSUES"),
    "create_pr": ("CREATE_PULL_REQUEST", "PULLS_CREATE"),
    "list_pr": ("PULLS_LIST", "LIST_PULL_REQUESTS"),
}
```

#### 5.1.3 Fuzzy Fallback

If no exact intent-to-action match is found, the system falls back to token-based matching:

1. Tokenize `intent_type` into keywords
2. For each tool, tokenize `tool.name` (split on `_`, lowercase)
3. Score = number of keyword matches
4. Keep tools with score >= 1, sorted by score DESC, up to `MAX_TOOLS_PER_INTENT`

```python
MAX_TOOLS_PER_INTENT: int = 8  # Cap on tools sent to LLM after action filter
```

### 5.2 New/Modified File: `components/Planner/adapters/tool_filter.py`

The existing file is extended with the new `filter_tools_by_action()` function. The existing `filter_tools_by_intent()` and `compact_tool_schemas()` remain unchanged.

```python
# New function added to existing tool_filter.py:

MAX_TOOLS_PER_INTENT: int = 8

_INTENT_ACTION_MAP: dict[str, tuple[str, ...]] = {
    # ... as defined in 5.1.2
}


def filter_tools_by_action(
    tools: Iterable[Any],
    intent_type: str | None,
    intent_entities: dict[str, Any] | None = None,
) -> list[Any]:
    """Second-pass filter: narrow to action-relevant tools within providers.

    Called AFTER filter_tools_by_intent() (provider filter).
    Fail-open: returns input list if filter produces 0 results.

    Args:
        tools: Provider-filtered tool list.
        intent_type: Intent action string.
        intent_entities: Optional entity dict for additional hints.

    Returns:
        Action-filtered list, capped at MAX_TOOLS_PER_INTENT.
    """
    all_tools = list(tools)
    if not intent_type or not all_tools:
        return all_tools

    # Pass 1: Exact intent-to-action map lookup
    needle = intent_type.lower()
    matched_actions: set[str] = set()
    for pattern, actions in _INTENT_ACTION_MAP.items():
        if pattern in needle:
            matched_actions.update(actions)

    if matched_actions:
        # Filter tools whose name contains any matched action
        filtered = [
            t for t in all_tools
            if any(action in getattr(t, "name", "").upper() for action in matched_actions)
        ]
        if filtered:
            return filtered[:MAX_TOOLS_PER_INTENT]

    # Pass 2: Fuzzy token matching fallback
    intent_tokens = _tokenize_intent(needle)
    if not intent_tokens:
        return all_tools

    scored: list[tuple[Any, int]] = []
    for tool in all_tools:
        tool_tokens = _tokenize_tool_name(getattr(tool, "name", ""))
        desc_tokens = _tokenize_tool_name(getattr(tool, "description", ""))
        all_tool_tokens = tool_tokens | desc_tokens
        match_count = len(intent_tokens & all_tool_tokens)
        if match_count > 0:
            scored.append((tool, match_count))

    if not scored:
        return all_tools  # fail-open

    scored.sort(key=lambda x: -x[1])
    return [t for t, _ in scored[:MAX_TOOLS_PER_INTENT]]


def _tokenize_intent(intent: str) -> set[str]:
    """Tokenize intent string into action keywords."""
    parts = re.split(r"[_\s.\-]+", intent.lower())
    return {p for p in parts if p and len(p) > 1 and p not in _FILTER_STOP_WORDS}


def _tokenize_tool_name(name: str) -> set[str]:
    """Tokenize MCP tool name into keywords."""
    parts = re.split(r"[_\s.\-]+", name.lower())
    return {p for p in parts if p and len(p) > 1 and p not in _FILTER_STOP_WORDS}


_FILTER_STOP_WORDS: frozenset[str] = frozenset({
    "the", "a", "an", "to", "for", "with", "from", "in", "on", "at",
    "by", "of", "and", "or", "is", "all",
})
```

### 5.3 Modified File: `components/Planner/service/planner_service.py`

In the `generate_plan()` method, add the action filter step after the existing provider filter:

```python
# After existing provider filter (line ~423):
tools = filter_tools_by_intent(tools, intent.intent)

# NEW: Action-level filter within providers
from components.Planner.adapters.tool_filter import filter_tools_by_action
pre_action_count = len(tools)
tools = filter_tools_by_action(tools, intent.intent, intent.entities)
if len(tools) != pre_action_count:
    logger.info(
        "tool_catalog_action_filtered intent=%s provider_filtered=%d action_filtered=%d",
        intent.intent, pre_action_count, len(tools),
    )

# Existing: compact schemas
tools = compact_tool_schemas(tools)
```

---

## 6. Data Model Changes

### 6.1 New Domain Model: `ScoredEvidence`

Location: `components/ContextRAG/adapters/evidence_scorer.py`

```python
@dataclass
class ScoredEvidence:
    item: EvidenceItem
    relevance: float = 0.0      # 0.0 to 1.0
    match_reasons: list[str] = field(default_factory=list)
```

This is a lightweight dataclass, not a Pydantic model, because it is internal to the scoring pipeline and never serialized to JSON or stored.

### 6.2 No Schema Changes

- `EvidenceItem` schema unchanged (shared/schemas/evidence.py)
- `ContextResult` model unchanged (components/ContextRAG/domain/models.py)
- `ToolDefinition` dataclass unchanged (shared/mcp/catalog.py)
- `Intent` schema unchanged (shared/schemas/intent.py)
- `Plan` schema unchanged (shared/schemas/plan.py)

All changes are internal pipeline optimizations. No contract changes, no schema migrations.

---

## 7. File Inventory

### 7.1 New Files

| File | Component | Purpose |
|------|-----------|---------|
| `components/ContextRAG/adapters/evidence_scorer.py` | ContextRAG | Intent-aware evidence relevance scoring |
| `components/ContextRAG/tests/test_evidence_scorer.py` | ContextRAG | Unit tests for EvidenceScorer |
| `components/Planner/tests/test_tool_action_filter.py` | Planner | Unit tests for action-level tool filtering |

### 7.2 Modified Files

| File | Component | Change Description |
|------|-----------|-------------------|
| `components/ContextRAG/adapters/budget_manager.py` | ContextRAG | Add optional `relevance_scores` param to `enforce_budget()` |
| `components/ContextRAG/service/context_rag_service.py` | ContextRAG | Integrate EvidenceScorer into gather_evidence() pipeline |
| `components/Planner/adapters/tool_filter.py` | Planner | Add `filter_tools_by_action()`, `_INTENT_ACTION_MAP`, token helpers |
| `components/Planner/service/planner_service.py` | Planner | Add action filter call after provider filter |

### 7.3 Unchanged Files (referenced for context)

| File | Reason |
|------|--------|
| `shared/schemas/evidence.py` | No schema changes |
| `shared/schemas/intent.py` | No schema changes |
| `shared/mcp/catalog.py` | `ToolDefinition` unchanged |
| `components/Planner/adapters/prompt_builder.py` | No changes needed (receives fewer items) |
| `components/ContextRAG/adapters/profilestore_adapter.py` | No changes |
| `components/ContextRAG/adapters/history_adapter.py` | No changes |
| `components/ContextRAG/adapters/planlibrary_adapter.py` | No changes |
| `components/ContextRAG/adapters/vectorindex_adapter.py` | No changes |

---

## 8. Sequences

### 8.1 Optimized Evidence Gathering (Happy Path)

```
Planner              ContextRAGService         EvidenceScorer       BudgetManager
  |                        |                        |                    |
  |--gather_evidence(intent)->                      |                    |
  |                        |                        |                    |
  |                        |--[concurrent fetch: ProfileStore, History,  |
  |                        |   PlanLibrary, VectorIndex]                 |
  |                        |                        |                    |
  |                        |<-all_evidence (9 items)|                    |
  |                        |                        |                    |
  |                        |--deduplicate()-------->|                    |
  |                        |                        |                    |
  |                        |--score_to_dict(intent, evidence)-->        |
  |                        |<-{key: relevance} (9 scores)-----|         |
  |                        |                        |                    |
  |                        |--enforce_budget(evidence, relevance_scores)->
  |                        |<-(3 items, 890 bytes)-------------------|
  |                        |                        |                    |
  |<-ContextResult(evidence=[3 items], total_bytes=890)                 |
```

### 8.2 Optimized Tool Filtering (Happy Path)

```
PlannerService         ToolCatalog          tool_filter.py
  |                        |                     |
  |--get_user_tools()----->|                     |
  |<-226 tools------------|                     |
  |                        |                     |
  |--filter_tools_by_intent(226 tools, "send_email")->
  |<-31 tools (gmail provider)-------------------|
  |                        |                     |
  |--filter_tools_by_action(31 tools, "send_email")->
  |<-4 tools (SEND_EMAIL, CREATE_DRAFT, GET_EMAIL, LIST_EMAILS)--|
  |                        |                     |
  |--compact_tool_schemas(4 tools)-------------->|
  |<-4 compacted tools--------------------------|
```

### 8.3 Fail-Open: Action Filter No Match

```
PlannerService                    tool_filter.py
  |                                    |
  |--filter_tools_by_action(31 tools, "novel_intent_xyz")-->
  |  (no match in _INTENT_ACTION_MAP)  |
  |  (fuzzy token match: 0 results)    |
  |<-31 tools (unchanged, fail-open)---|
```

### 8.4 Fail-Open: Evidence Scorer With No Relevance

```
ContextRAGService      EvidenceScorer        BudgetManager
  |                        |                     |
  |--score_to_dict(intent, evidence)-->          |
  |<-{all keys: 0.1} (low scores)-----|          |
  |                                    |          |
  |--enforce_budget(evidence, scores)----------->|
  |  (relevance-sorted, but low scores)          |
  |  (MIN_EVIDENCE_ITEMS=2 floor enforced)       |
  |<-(2 items, ~500 bytes)----------------------|
```

---

## 9. Implementation Order

Implementation follows the principle of building foundations first (scoring algorithms), then integrating them into the pipeline, then testing end-to-end.

### Phase 1: Foundation (No existing code changes)

| Order | Task | File | Description |
|-------|------|------|-------------|
| 1.1 | Create EvidenceScorer | `components/ContextRAG/adapters/evidence_scorer.py` | Pure module, no dependencies on existing code changes |
| 1.2 | Create EvidenceScorer tests | `components/ContextRAG/tests/test_evidence_scorer.py` | Unit tests for all scoring signals |
| 1.3 | Add action filter to tool_filter | `components/Planner/adapters/tool_filter.py` | Add `filter_tools_by_action()` and `_INTENT_ACTION_MAP` |
| 1.4 | Create action filter tests | `components/Planner/tests/test_tool_action_filter.py` | Unit tests for action-level filtering |

### Phase 2: Integration (Modify existing code)

| Order | Task | File | Description |
|-------|------|------|-------------|
| 2.1 | Modify BudgetManager | `components/ContextRAG/adapters/budget_manager.py` | Add `relevance_scores` param to `enforce_budget()` |
| 2.2 | Update BudgetManager tests | `components/ContextRAG/tests/test_unit.py` | Add tests for relevance-aware sorting |
| 2.3 | Integrate scorer into ContextRAGService | `components/ContextRAG/service/context_rag_service.py` | Wire EvidenceScorer into gather_evidence() |
| 2.4 | Integrate action filter into PlannerService | `components/Planner/service/planner_service.py` | Add action filter call after provider filter |

### Phase 3: Verification

| Order | Task | File | Description |
|-------|------|------|-------------|
| 3.1 | Update ContextRAG service tests | `components/ContextRAG/tests/test_service.py` | Verify scorer integration, backward compat |
| 3.2 | Update Planner service tests | `components/Planner/tests/test_unit.py` | Verify action filter in generate_plan flow |
| 3.3 | Add metrics logging | Both service files | Log evidence count before/after scoring, tool count before/after action filter |
| 3.4 | Add observability tests | Test files | Verify no PII in new log messages |

---

## 10. Test Strategy

### 10.1 EvidenceScorer Tests (`components/ContextRAG/tests/test_evidence_scorer.py`)

```python
class TestTokenize:
    """Test the _tokenize helper."""
    def test_snake_case_splits(self): ...
    def test_camel_case_splits(self): ...
    def test_stop_words_removed(self): ...
    def test_single_char_removed(self): ...
    def test_empty_string(self): ...

class TestEvidenceScorer:
    """Test the EvidenceScorer.score() method."""

    # Keyword overlap signal
    def test_high_keyword_overlap_scores_high(self):
        """Intent 'schedule_meeting' + evidence key 'meeting_duration_min' -> high score."""

    def test_no_keyword_overlap_scores_low(self):
        """Intent 'send_email' + evidence key 'meeting_duration_min' -> low score."""

    # Entity match signal
    def test_entity_value_exact_match_scores_high(self):
        """Evidence value matches intent entity value -> entity score = 1.0."""

    def test_entity_key_match_scores_medium(self):
        """Evidence key matches intent entity key name -> entity score = 0.8."""

    # Type affinity signal
    def test_email_intent_prefers_contact_type(self):
        """'send_email' intent -> contact-type evidence scores higher."""

    def test_meeting_intent_prefers_preference_type(self):
        """'schedule_meeting' intent -> preference-type evidence scores higher."""

    def test_unknown_intent_all_types_relevant(self):
        """Unknown intent -> all evidence types score equally on affinity."""

    # Composite scoring
    def test_multiple_signals_combine(self):
        """Evidence matching on multiple signals scores highest."""

    def test_empty_evidence_returns_empty(self): ...
    def test_empty_intent_returns_all_with_base_scores(self): ...

    # Determinism
    def test_same_inputs_same_scores(self):
        """Verify determinism: same intent + evidence -> identical scores."""

    # score_to_dict convenience
    def test_score_to_dict_returns_correct_keys(self): ...
```

**Target: ~15 tests**

### 10.2 Action Filter Tests (`components/Planner/tests/test_tool_action_filter.py`)

```python
class TestFilterToolsByAction:
    """Test the filter_tools_by_action() function."""

    # Exact match via _INTENT_ACTION_MAP
    def test_send_email_filters_to_send_and_draft(self):
        """'send_email' intent -> SEND_EMAIL, CREATE_DRAFT tools."""

    def test_schedule_meeting_filters_to_calendar_actions(self):
        """'schedule_meeting' intent -> CREATE_EVENT, FIND_FREE_SLOTS, etc."""

    def test_list_meetings_filters_to_read_actions(self):
        """'list_meetings' intent -> FIND_EVENT, LIST_EVENTS only."""

    # Fuzzy fallback
    def test_fuzzy_match_on_unknown_intent(self):
        """'analyze_data' intent (not in map) -> fuzzy token match."""

    # Fail-open behavior
    def test_no_match_returns_full_list(self):
        """Unrecognizable intent -> returns all tools unchanged."""

    def test_empty_intent_returns_full_list(self): ...
    def test_empty_tools_returns_empty(self): ...

    # Max tools cap
    def test_caps_at_max_tools(self):
        """Even with many matches, caps at MAX_TOOLS_PER_INTENT."""

    # Interaction with existing provider filter
    def test_provider_then_action_produces_minimal_set(self):
        """Chain: 226 -> provider filter -> 31 -> action filter -> 5."""

    # Token helpers
    def test_tokenize_intent_splits_correctly(self): ...
    def test_tokenize_tool_name_splits_correctly(self): ...
```

**Target: ~12 tests**

### 10.3 BudgetManager Update Tests (addition to `components/ContextRAG/tests/test_unit.py`)

```python
class TestBudgetManagerRelevanceScoring:
    """Test enforce_budget() with relevance_scores parameter."""

    def test_relevance_scores_affect_sort_order(self):
        """High-relevance items kept even if lower tier/confidence."""

    def test_no_relevance_scores_uses_existing_sort(self):
        """Backward compat: None scores -> tier+confidence sort."""

    def test_min_evidence_floor_enforced(self):
        """At least MIN_EVIDENCE_ITEMS returned even with tight budget."""

    def test_relevance_sort_is_deterministic(self):
        """Same scores -> same output order."""
```

**Target: ~4 tests**

### 10.4 Integration Tests (existing test files, new cases)

```python
# In components/ContextRAG/tests/test_service.py
class TestContextRAGServiceWithScorer:
    def test_scorer_reduces_evidence_count(self): ...
    def test_scorer_disabled_preserves_existing_behavior(self): ...
    def test_scorer_failure_falls_back_to_unscored(self): ...

# In components/Planner/tests/test_unit.py (or separate test_service.py)
class TestPlannerServiceWithActionFilter:
    def test_action_filter_reduces_tool_count(self): ...
    def test_action_filter_fail_open_on_unknown_intent(self): ...
```

**Target: ~5 tests**

### 10.5 Test Summary

| Category | File | Count |
|----------|------|-------|
| EvidenceScorer unit | `components/ContextRAG/tests/test_evidence_scorer.py` | ~15 |
| Action filter unit | `components/Planner/tests/test_tool_action_filter.py` | ~12 |
| BudgetManager update | `components/ContextRAG/tests/test_unit.py` (additions) | ~4 |
| Integration | Existing test files (additions) | ~5 |
| **Total** | | **~36** |

---

## 11. Observability

### 11.1 New Log Events

| Event | Level | Component | Extra Fields |
|-------|-------|-----------|-------------|
| `evidence_scored` | INFO | ContextRAG | `intent_type`, `total_evidence`, `scored_above_threshold`, `top_score`, `duration_us` |
| `evidence_pruned_by_relevance` | INFO | ContextRAG | `intent_type`, `before_count`, `after_count`, `pruned_keys` (evidence keys only, no values) |
| `tool_catalog_action_filtered` | INFO | Planner | `intent_type`, `provider_filtered`, `action_filtered`, `kept_tools` (tool names only) |

### 11.2 No PII in New Logs

- Evidence keys are logged (e.g., `meeting_duration_min`), NOT values
- Tool names are logged (e.g., `GMAIL_SEND_EMAIL`), which are non-sensitive
- Intent type is logged (e.g., `send_email`), which is an action label
- No entity values, no user data, no preference values

### 11.3 Metrics (Prometheus)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `contextrag_evidence_scored_total` | counter | `intent_type` | Evidence items scored per request |
| `contextrag_evidence_kept_after_scoring` | histogram | `intent_type` | How many items survive scoring |
| `contextrag_scoring_duration_seconds` | histogram | -- | Time spent in EvidenceScorer |
| `planner_tool_action_filter_input` | histogram | `intent_type` | Tools before action filter |
| `planner_tool_action_filter_output` | histogram | `intent_type` | Tools after action filter |

---

## 12. Non-Functional Requirements

### 12.1 Performance Impact

| Operation | Current | After Optimization | Notes |
|-----------|---------|-------------------|-------|
| `gather_evidence()` p95 | < 150 ms | < 155 ms (+ ~5ms scoring) | Scoring is pure CPU, no I/O |
| Evidence items in prompt | ~9 | 2-4 | 55-75% reduction |
| Tools in prompt | ~31 | 3-8 | 75-90% reduction |
| Estimated input tokens | ~15K-25K | ~4K-8K | 60-80% reduction |
| LLM call latency (estimated) | 20-24s | 5-10s | Proportional to input size |
| `generate_plan()` total | 22-26s | 7-12s | Net improvement |

### 12.2 Scoring Computational Cost

EvidenceScorer is O(E * T) where:
- E = number of evidence items (typically 5-15)
- T = number of tokens per item (typically 3-8)

Expected wall-clock time: < 1ms for 15 evidence items. Well within the 5ms budget.

Action filter is O(T * A) where:
- T = number of tools after provider filter (typically 20-40)
- A = number of action keywords (typically 3-8)

Expected wall-clock time: < 0.5ms. Negligible.

---

## 13. Dependencies

### 13.1 Python Packages

No new packages required. All scoring and filtering uses only:
- `re` (standard library) -- tokenization
- `dataclasses` (standard library) -- `ScoredEvidence`
- Existing Pydantic models from `shared/schemas/`

### 13.2 Internal Component Dependencies

| Change | Depends On | Notes |
|--------|-----------|-------|
| EvidenceScorer | `shared.schemas.evidence.EvidenceItem`, `shared.schemas.intent.Intent` | Read-only, no mutations |
| BudgetManager changes | EvidenceScorer (optional) | Backward-compatible param |
| ContextRAGService changes | EvidenceScorer | Optional init param |
| PlannerService changes | `filter_tools_by_action` (same module) | New function in existing file |

No new cross-component dependencies introduced.

---

## 14. Architectural Considerations

### 14.1 Determinism

Both the EvidenceScorer and the action filter are pure functions:
- Same intent + same evidence = same scores (no randomness)
- Same intent + same tools = same filtered set (deterministic map lookup + tokenization)
- Sort stability is preserved (Python's `sorted()` is stable)

This maintains the GLOBAL_SPEC v3.1 deterministic planning guarantee: same frozen tuple inputs produce the same plan.

### 14.2 Backward Compatibility

All changes are backward-compatible:
- `BudgetManager.enforce_budget(evidence)` (no `relevance_scores`) works identically to current behavior
- `ContextRAGService.__init__()` without `evidence_scorer` creates a default scorer (opt-in is automatic, but behavior degrades gracefully if scorer is disabled)
- `filter_tools_by_action()` with `None` intent returns all tools
- Existing tests do not need modification (new behavior is additive)

### 14.3 Configuration

Two new optional environment variables (with sensible defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `EVIDENCE_SCORING_ENABLED` | `true` | Enable/disable relevance scoring (string "true"/"false") |
| `TOOL_ACTION_FILTER_ENABLED` | `true` | Enable/disable action-level tool filtering |

These allow operators to disable either optimization independently if issues arise in production.

### 14.4 Risks

| Risk | Severity | Probability | Mitigation |
|------|----------|-------------|------------|
| Scoring prunes critical evidence | Medium | Low | MIN_EVIDENCE_ITEMS floor; always keep at least 1 preference item |
| Action filter removes needed tool | Medium | Low | Fail-open design; falls back to provider-filtered set |
| Keyword maps incomplete for new providers | Low | Medium | Fuzzy fallback catches most cases; maps are easy to extend |
| Scoring overhead exceeds 5ms | Low | Very Low | Pure CPU, O(n*m) with small n and m |
| LLM plan quality degrades with less context | Medium | Low | Monitor fallback_level distribution; A/B test before full rollout |

---

## 15. Open Questions

1. **A/B testing**: Should we run both paths (scored vs unscored) in parallel for the first N requests to compare plan quality? Recommendation: Log both scored and unscored evidence counts, but only send scored evidence to LLM. Compare plan success rates over 1 week.

2. **Dynamic action maps**: Should `_INTENT_ACTION_MAP` be stored in config/database instead of hardcoded? Recommendation: Hardcode for MVP. The map is small (< 50 entries) and changes rarely. Move to config if we add providers frequently.

3. **User feedback signal**: Should scoring incorporate user feedback on plan quality (implicit signal)? Recommendation: Deferred. Current signals (keyword, entity, affinity, confidence) are sufficient for MVP. History-based scoring can be added as a future 5th signal.

4. **Token counting**: Should we measure actual token count (via tiktoken) instead of byte count for the budget? Recommendation: Deferred. Byte count is a reasonable proxy, and tiktoken adds a dependency. Can be added later if budget precision matters.

---

## 16. Post-Generation Validation Checklist

- [x] No schema changes (all changes are internal pipeline optimization)
- [x] No new database tables or Redis keys
- [x] No new HTTP routes
- [x] No contract changes between components
- [x] Backward-compatible API: all new parameters are optional with defaults
- [x] Fail-open design on both improvements (never produces fewer results than empty)
- [x] Deterministic scoring (pure functions, no randomness)
- [x] No PII in new log messages (evidence keys only, tool names only)
- [x] No new Python package dependencies
- [x] Test strategy covers all new code paths with ~36 tests
- [x] Existing tests unaffected (backward-compatible changes)
- [x] Feature flags available (`EVIDENCE_SCORING_ENABLED`, `TOOL_ACTION_FILTER_ENABLED`)
