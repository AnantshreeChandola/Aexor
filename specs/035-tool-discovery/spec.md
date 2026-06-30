# Feature Specification: Tool Discovery — 3-Tier Hybrid Retrieval

**Feature Branch**: `feat/tool-discovery`
**Created**: 2026-05-01
**Status**: Implemented
**Input**: Replace hardcoded keyword-based tool filtering with embedding retrieval (plan-based + tool-based), cross-encoder ONNX reranking, and agentic fallback for self-healing tool resolution

## Overview

Tool filtering in the Planner uses hardcoded keyword maps (`_INTENT_PROVIDER_MAP`, `_INTENT_ACTION_MAP` in `components/Planner/adapters/tool_filter.py`) that map intent substrings to provider allowlists and action names. Every new intent or tool provider requires manual map updates. The maps are brittle — they can't generalize to unseen intents, and they fail-open to the full catalog (~200 tools, ~180KB of JSON) when nothing matches, blowing up LLM prompt size and cost.

Tool Discovery replaces this with a 3-tier hybrid pipeline:

1. **Tier 1 — Embedding retrieval** (~20 candidates): Combines (A) mining past plan embeddings to find proven tool combinations for similar intents (top 10), with (B) direct semantic search over tool description embeddings to cover new/unused tools (top 10).
2. **Tier 2 — Cross-encoder reranking** (top 5): An ONNX cross-encoder model (`ms-marco-MiniLM-L-6-v2`, ~80MB) scores `(intent, tool_description)` pairs and selects the top 5 most relevant tools.
3. **Tier 3 — Agentic fallback** (self-healing): When the LLM generates a plan referencing a tool not in the provided set, the system automatically searches tool embeddings by name, resolves the canonical tool, and retries — without user intervention.

The existing keyword filter (`tool_filter.py`) is preserved as the graceful-degradation fallback when VectorIndex or the cross-encoder model is unavailable.

**Cost**: $0 in API fees. Both the bi-encoder (existing `all-MiniLM-L6-v2`) and cross-encoder (new `ms-marco-MiniLM-L-6-v2`) run locally via ONNX Runtime.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Tool Embedding Storage & Sync (Priority: P1)

When the ToolCatalog refreshes (hourly or on user connection change), all tool definitions are embedded and stored in the `tool_embeddings` table. Each tool's search text is built from its provider name, action words, description, and parameter names.

**Why this priority**: Without stored tool embeddings, Tier 1B (direct tool search) cannot function. This is the foundational write path for tool discovery.

**Independent Test**: Trigger a catalog refresh with 5 mock tools, then query `tool_embeddings` directly to confirm 5 rows exist with correct 384-dim vectors, populated tsvectors, correct tool names, and provider names.

**Acceptance Scenarios**:

1. **Given** a ToolCatalog with 5 tools, **When** `sync_tool_embeddings(tools)` is called, **Then** 5 rows are upserted into `tool_embeddings` with 384-dim vectors, non-empty tsvectors, and correct `tool_name` / `provider_name`.
2. **Given** a tool `GOOGLECALENDAR_CREATE_EVENT` with description "Create a new calendar event" and input_schema with properties `start_time`, `summary`, `attendees`, **When** `build_tool_search_text(tool)` is called, **Then** it returns `"googlecalendar create event Create a new calendar event start_time summary attendees"`.
3. **Given** the same tool is synced twice, **When** `sync_tool_embeddings` is called again, **Then** the embedding is upserted (updated, not duplicated) and `updated_at` is refreshed.
4. **Given** a ToolCatalog refresh completes, **When** the refresh callback fires, **Then** `sync_tool_embeddings` is called with the new tool list.
5. **Given** `sync_tool_embeddings` fails (e.g., DB error), **Then** a warning is logged and the catalog refresh is NOT blocked (fire-and-forget).

---

### User Story 2 — Plan-Based Tool Discovery (Priority: P1)

When a user submits an intent, the system searches existing plan embeddings (`plan_embeddings` table) to find similar past plans, loads their full plan data, and extracts the tools that were used — providing "proven" tool combinations for similar intents.

**Why this priority**: Co-P1 with tool embeddings. Plan-based discovery is the highest-signal source because it uses real execution history. Tools that appear in multiple matching plans have high confidence.

**Independent Test**: Store 3 plan embeddings for "schedule_meeting" plans that use `GOOGLECALENDAR_CREATE_EVENT` and `GOOGLECALENDAR_LIST_EVENTS`. Search with intent "book a meeting with Alice" (top_k=10). Verify both tools are returned with frequency scores proportional to how many plans used them.

**Acceptance Scenarios**:

1. **Given** 5 stored plans for "schedule_meeting" all using `GOOGLECALENDAR_CREATE_EVENT`, **When** plan-based discovery runs for intent "schedule a team meeting", **Then** `GOOGLECALENDAR_CREATE_EVENT` is returned with frequency score 1.0 (5/5 plans).
2. **Given** 5 plans where 3 use `GOOGLECALENDAR_LIST_EVENTS` and 2 don't, **When** discovery runs, **Then** `GOOGLECALENDAR_LIST_EVENTS` is returned with frequency score 0.6 (3/5).
3. **Given** no matching plans exist (fresh install), **When** plan-based discovery runs, **Then** it returns an empty dict (no tools) and Tier 1B provides coverage.
4. **Given** a plan step with `uses: "system.noop"` (virtual tool), **When** tools are extracted, **Then** `system.noop` is excluded from results.
5. **Given** `VectorIndexService.search()` is called with `top_k=10`, **When** fewer than 10 plans match, **Then** all matching plans are used (no error).

---

### User Story 3 — Direct Tool Embedding Search (Priority: P1)

When plan-based discovery returns insufficient tools (e.g., novel intent, no history), the system searches `tool_embeddings` directly by the intent text using hybrid BM25 + cosine search, returning tools whose descriptions semantically match the user's intent.

**Why this priority**: Covers the cold-start problem — new tools that have never appeared in a plan, and novel intents with no plan history.

**Independent Test**: Store embeddings for 20 tools including `GMAIL_SEND_EMAIL` (description: "Send an email via Gmail"). Search with intent "email the report to the team". Verify `GMAIL_SEND_EMAIL` appears in top results.

**Acceptance Scenarios**:

1. **Given** 20 tool embeddings including `GMAIL_SEND_EMAIL`, **When** `search_by_intent("send an email to Alice", top_k=10)` is called, **Then** `GMAIL_SEND_EMAIL` appears in the top 5 results.
2. **Given** a tool with description mentioning "calendar" and parameters `start_time`, `attendees`, **When** searching for "book a meeting room", **Then** the tool ranks high due to semantic similarity (even though "meeting room" ≠ "calendar").
3. **Given** `top_k=10`, **When** only 5 tools exist, **Then** all 5 are returned (no error, no padding).
4. **Given** a search with no BM25 matches but good semantic matches, **Then** semantic-only results are returned (RRF degrades gracefully).

---

### User Story 4 — Merge, Intersect & Validate Connected Tools (Priority: P1)

Tier 1A (plan-based) and Tier 1B (tool-based) results are merged into a union set, then intersected with the user's actually connected tools — only tools the user has OAuth access to are included. If high-confidence tools from plan-based discovery (Tier 1A) are missing from the user's connected tools, the system raises a `ToolNotConnectedError` to inform the user which tools they need to connect.

**Why this priority**: Without intersection, the system would suggest tools the user can't execute. Without error reporting, the user would never know they need to connect a tool — the plan would silently degrade or fail at execution time.

**Acceptance Scenarios**:

1. **Given** Tier 1A returns `{GOOGLECALENDAR_CREATE_EVENT, GMAIL_SEND_EMAIL}` and Tier 1B returns `{GMAIL_SEND_EMAIL, SLACK_SEND_MESSAGE}`, **When** merged, **Then** the union is `{GOOGLECALENDAR_CREATE_EVENT, GMAIL_SEND_EMAIL, SLACK_SEND_MESSAGE}`.
2. **Given** the user only has Google Calendar and Gmail connected (no Slack), **When** intersected with `available_tools`, **Then** `SLACK_SEND_MESSAGE` is excluded from the candidate set (it came from Tier 1B only, not plan-proven).
3. **Given** the merged set has >20 candidates, **When** capped at `max_candidates`, **Then** only the top 20 (by combined score) are passed to Tier 2.
4. **Given** Tier 1A (plan-based) returns `{GOOGLECALENDAR_CREATE_EVENT}` with frequency >= 0.5, **When** the user has NOT connected Google Calendar, **Then** a `ToolNotConnectedError` is raised listing `GOOGLECALENDAR_CREATE_EVENT` as a required but unconnected tool, with the provider name `"googlecalendar"` for the frontend to display a "Connect this tool" prompt.
5. **Given** Tier 1A returns `{GOOGLECALENDAR_CREATE_EVENT, SLACK_SEND_MESSAGE}` both with high frequency, **When** the user has Google Calendar but NOT Slack connected, **Then** a `ToolNotConnectedError` is raised listing only `SLACK_SEND_MESSAGE` (the missing one).
6. **Given** only Tier 1B (direct tool search) suggests a tool the user hasn't connected, **When** the merge runs, **Then** NO error is raised — the tool is silently excluded. (Errors are only raised for plan-proven tools from Tier 1A with high confidence, not speculative Tier 1B matches.)
7. **Given** the merged set has <3 candidates after intersection AND no `ToolNotConnectedError` was raised (no high-confidence tools missing), **When** the `min_threshold` check runs, **Then** the system fails-open to the full `available_tools` list.

---

### User Story 5 — Cross-Encoder Reranking (Priority: P1)

The top ~20 candidates from Tier 1 are reranked by an ONNX cross-encoder model that scores each `(intent_text, tool_description)` pair, returning the top 5 tools sorted by relevance.

**Why this priority**: The bi-encoder (Tier 1) provides recall; the cross-encoder provides precision. Without reranking, the top-5 selection would be based on coarse embedding similarity alone.

**Independent Test**: Provide 15 candidate tools and an intent. Verify the cross-encoder scores all 15, sorts by score, and returns only the top 5.

**Acceptance Scenarios**:

1. **Given** 15 candidate tools and intent "schedule a meeting", **When** `rerank(intent, candidates, top_k=5)` is called, **Then** exactly 5 tools are returned, sorted by descending cross-encoder score.
2. **Given** a tool with description "Create a new calendar event with title, time, attendees" and intent "book a meeting with Alice", **Then** it scores higher than a tool with description "List all files in a directory".
3. **Given** 3 candidates (fewer than `top_k=5`), **When** reranked, **Then** all 3 are returned (no error, no padding).
4. **Given** the cross-encoder ONNX model is unavailable, **When** Tier 2 is attempted, **Then** it is skipped (not an error), Tier 1 results are passed through sorted by Tier 1 scores, and a warning is logged.
5. **Given** 20 candidates, **When** reranked, **Then** the batched ONNX forward pass completes in <50ms on CPU.

---

### User Story 6 — Agentic Fallback on Unresolved Tools (Priority: P2)

During plan finalization (`_finalize_plan`), when `resolve_tool()` fails for a step (the LLM referenced a tool not in the provided catalog), the system automatically searches `tool_embeddings` by the unresolved tool name, maps to a canonical tool, and substitutes it.

**Why this priority**: This is a self-healing mechanism. It prevents plan generation failures when the LLM hallucinates a slightly wrong tool name or when Tier 1+2 missed a needed tool. P2 because Tier 1+2 should handle most cases.

**Independent Test**: Generate a plan where the LLM outputs `uses: "google.calendar"` but the canonical name is `GOOGLECALENDAR_CREATE_EVENT`. Verify agentic expand finds and substitutes the correct tool.

**Acceptance Scenarios**:

1. **Given** a plan step with `uses: "google_calendar_create_event"` (non-canonical name), **When** `resolve_tool()` fails, **Then** `agentic_expand("google_calendar_create_event", ...)` searches tool embeddings and finds `GOOGLECALENDAR_CREATE_EVENT`.
2. **Given** `agentic_expand` finds a matching tool, **When** the tool is in the user's `available_tools`, **Then** the step's `uses` field is updated to the canonical name and the plan is valid.
3. **Given** `agentic_expand` finds no matching tools, **Then** the original `ToolNotAvailableError` is raised (existing behavior preserved).
4. **Given** multiple unresolved tools in a plan, **When** agentic expand runs, **Then** each is resolved independently (one failure doesn't block others).
5. **Given** `ToolDiscoveryService` is `None` (VectorIndex unavailable), **When** `_finalize_plan` has unresolved tools, **Then** existing behavior is unchanged (no agentic fallback attempted).

---

### User Story 7 — Graceful Degradation to Keyword Filter (Priority: P1)

When VectorIndex, the embedding model, or the cross-encoder model is unavailable, the system falls back to the existing keyword-based filtering pipeline (`filter_tools_by_intent` + `filter_tools_by_action`), preserving current behavior.

**Why this priority**: The system must never fail to generate a plan due to tool discovery infrastructure issues. The keyword filter is battle-tested and must remain the safety net.

**Acceptance Scenarios**:

1. **Given** `TOOL_DISCOVERY_ENABLED=false`, **When** `generate_plan()` runs, **Then** the legacy keyword filter is used and `ToolDiscoveryService` is never invoked.
2. **Given** pgvector extension is unavailable, **When** app starts, **Then** `ToolDiscoveryService` is not created and `planner_service._tool_discovery` is `None`.
3. **Given** `discover_tools()` raises an exception, **When** caught in `generate_plan()`, **Then** the legacy keyword filter is used and a warning is logged.
4. **Given** the cross-encoder model file is missing, **When** `CrossEncoderReranker` init fails, **Then** `ToolDiscoveryService` is created without a reranker (Tier 2 is skipped, Tier 1 results passed through).

---

### Edge Cases

- What happens when the tool catalog is empty (no tools at all)? -> `discover_tools` returns empty list; `compact_tool_schemas` receives empty list; LLM prompt says "No tools available" — same as existing behavior.
- What happens on concurrent `sync_tool_embeddings` calls? -> PostgreSQL ON CONFLICT handles upserts; no application-level locking needed.
- What if a tool's description is empty? -> `build_tool_search_text` still produces `"{provider} {action_words} {param_names}"` — BM25 and semantic search still function, just with less signal.
- What if `plan_embeddings` table has 0 rows (fresh install)? -> Tier 1A returns nothing; Tier 1B provides full coverage; Tier 2 reranks whatever Tier 1B found.
- What if both Tier 1A and 1B return 0 candidates? -> Fails-open to full `available_tools` list (same as current keyword filter fail-open).
- What if the user has 0 connected tools? -> `NoToolsConnectedError` is raised immediately. The system does NOT fall back to `get_all_tools()` or pass an empty catalog to the LLM.
- What if a plan-proven tool (Tier 1A, high frequency) isn't connected? -> `ToolNotConnectedError` is raised with the missing tool names and providers. The API returns a 422 with `error_code: "TOOL_NOT_CONNECTED"` and the list of missing tools.
- What if the user explicitly passes `skip_tool_check=true`? -> The connected-tool validation is skipped and unconnected tools are silently excluded (degraded mode). This escape hatch supports cases where the user wants to plan even without all tools connected.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST search existing `plan_embeddings` by intent text and extract tools used in matching plans (Tier 1A).
- **FR-002**: System MUST generate 384-dim embeddings for tool descriptions using the existing `all-MiniLM-L6-v2` ONNX model and store them in a `tool_embeddings` table (Tier 1B).
- **FR-003**: System MUST support hybrid BM25 + cosine search over `tool_embeddings` via RRF, reusing the same query pattern as `plan_embeddings`.
- **FR-004**: System MUST merge Tier 1A (plan-based) and Tier 1B (tool-based) results and intersect with the user's connected tools.
- **FR-005**: System MUST rerank Tier 1 candidates using an ONNX cross-encoder model (`ms-marco-MiniLM-L-6-v2`) that scores `(intent, tool_description)` pairs (Tier 2).
- **FR-006**: System MUST support agentic fallback: when `resolve_tool()` fails during plan finalization, search `tool_embeddings` by the unresolved name and substitute the canonical match (Tier 3).
- **FR-007**: System MUST sync tool embeddings whenever the ToolCatalog refreshes (via callback).
- **FR-008**: System MUST fall back to existing keyword filter (`filter_tools_by_intent` + `filter_tools_by_action`) when VectorIndex is unavailable or `discover_tools()` fails.
- **FR-009**: System MUST support a `TOOL_DISCOVERY_ENABLED` master switch to disable the new pipeline entirely.
- **FR-010**: System MUST cap Tier 1A at 10 tools, Tier 1B at 10 tools, merged Tier 1 output at `TOOL_DISCOVERY_MAX_CANDIDATES` (default 20), and Tier 2 output at `TOOL_DISCOVERY_MAX_RERANKED` (default 5).
- **FR-011**: System MUST fail-open to the full `available_tools` list when Tier 1 + Tier 2 produce fewer than `TOOL_DISCOVERY_MIN_THRESHOLD` (default 3) tools AND no `ToolNotConnectedError` was raised.
- **FR-012**: All inference MUST run locally via ONNX Runtime. $0 external API cost.
- **FR-013**: System MUST raise a `ToolNotConnectedError` during tool discovery when plan-based discovery (Tier 1A) identifies high-confidence tools (frequency >= 0.5) that the user has not connected. The error MUST include the list of missing tool names and their provider names so the frontend can prompt the user to connect them.
- **FR-014**: `generate_plan()` MUST only use the user's linked tools (`get_user_tools(user_id)`). It MUST NOT fall back to `get_all_tools()`. If the user has no linked tools, the system MUST raise a `NoToolsConnectedError` instead of silently using the global catalog. This applies to all code paths: plan generation (line 1082), entity validation (line 416), and tool search (line 996).

### Key Entities

- **ToolEmbedding**: A stored tool representation for hybrid search. Attributes: `tool_name` (str, unique), `provider_name` (str), `embedding` (vector(384)), `tsv` (tsvector), `search_text` (str), `model_version` (str), `created_at`, `updated_at`.
- **ToolEmbeddingResult**: A search result from `tool_embeddings`. Attributes: `tool_name` (str), `provider_name` (str), `rrf_score` (float), `keyword_rank` (int | None), `semantic_rank` (int | None).
- **ToolDiscoveryResult**: The output of the full 3-tier pipeline. Attributes: `tools` (list[ToolDefinition]), `discovery_tier` (int: 0=fallback, 1=embedding, 2=reranked, 3=agentic), `candidate_count` (int), `reranked_count` (int), `plan_based_tools` (int), `direct_tools` (int), `discovery_ms` (int).
- **ToolNotConnectedError**: Raised when plan-based discovery identifies high-confidence tools the user hasn't connected. Attributes: `missing_tools` (list[dict] with `tool_name` and `provider_name`), `message` (str). This propagates to the API layer as a 422 response with `error_code: "TOOL_NOT_CONNECTED"` so the frontend can show a "Connect this tool" prompt.
- **NoToolsConnectedError**: Raised when the user has zero linked tools. Attributes: `user_id` (str), `message` (str). Propagates as a 422 response with `error_code: "NO_TOOLS_CONNECTED"`. Prevents the system from silently falling back to the global catalog (`get_all_tools()`).

---

## Interfaces & Contracts

### ToolDiscoveryService (library component — no HTTP routes)

ToolDiscovery is a **library component** consumed by `PlannerService`. It orchestrates all 3 tiers.

```python
class ToolDiscoveryService:
    def __init__(
        self,
        tool_embedding_adapter: ToolEmbeddingAdapter,
        reranker: CrossEncoderReranker | None,
        vector_index_service: VectorIndexService | None,
        plan_service: Any,
        max_candidates: int = 20,
        max_reranked: int = 5,
        min_tools_threshold: int = 3,
    ) -> None: ...

    async def discover_tools(
        self,
        intent_text: str,
        available_tools: list[ToolDefinition],
        intent_entities: dict[str, Any] | None = None,
        skip_tool_check: bool = False,
    ) -> ToolDiscoveryResult:
        """Run Tier 1 (retrieval) + Tier 2 (reranking). Returns ranked tool list.

        Raises ToolNotConnectedError if plan-based discovery identifies
        high-confidence tools the user hasn't connected (unless skip_tool_check=True).
        """
        ...

    async def agentic_expand(
        self,
        missing_tool_name: str,
        available_tools: list[ToolDefinition],
        current_selected: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        """Tier 3: search tool_embeddings by name, return newly discovered tools."""
        ...
```

### ToolEmbeddingAdapter

Manages the `tool_embeddings` table — embedding, storing, and searching tool definitions.

```python
class ToolEmbeddingAdapter:
    def __init__(
        self,
        embedding_adapter: EmbeddingAdapter,  # reuse existing ONNX adapter
        db_adapter: SharedDatabaseAdapter,
    ) -> None: ...

    async def sync_tool_embeddings(self, tools: list[ToolDefinition]) -> int:
        """Upsert embeddings for all tools. Returns count stored."""
        ...

    async def search_by_intent(
        self, intent_text: str, top_k: int = 10,
    ) -> list[ToolEmbeddingResult]:
        """Hybrid BM25 + cosine search over tool_embeddings."""
        ...

    async def search_by_tool_name(
        self, tool_name: str, top_k: int = 10,
    ) -> list[ToolEmbeddingResult]:
        """Search by tool name fragment (for Tier 3 agentic fallback)."""
        ...

    @staticmethod
    def build_tool_search_text(tool: ToolDefinition) -> str:
        """Build: '{provider} {action_words} {description} {param_names}'"""
        ...
```

### CrossEncoderReranker

ONNX cross-encoder for Tier 2.

```python
class CrossEncoderReranker:
    def __init__(self, model_path: str) -> None:
        """Load ONNX cross-encoder model and tokenizer."""
        ...

    def rerank(
        self,
        query: str,
        candidates: list[ToolDefinition],
        top_k: int = 5,
    ) -> list[tuple[ToolDefinition, float]]:
        """Score (query, tool.description) pairs. Returns sorted (tool, score)."""
        ...
```

### Tool Search Text Format

Each tool is embedded using a structured text representation:

```
"{provider} {action_words} {description} {parameter_names}"
```

Example for `GOOGLECALENDAR_CREATE_EVENT`:
```
"googlecalendar create event Creates a new event in Google Calendar start_time end_time summary attendees location timezone"
```

Construction:
- `provider`: First segment of tool name, lowercased (`GOOGLECALENDAR` -> `googlecalendar`)
- `action_words`: Remaining segments, lowercased, space-joined (`CREATE_EVENT` -> `create event`)
- `description`: `ToolDefinition.description` as-is
- `parameter_names`: Keys from `input_schema.properties`, space-joined

### Hybrid Search SQL (tool_embeddings)

Same RRF pattern as `plan_embeddings` (`pgvector_adapter.py:89-174`), querying `tool_embeddings`:

```sql
WITH keyword AS (
  SELECT tool_name, provider_name,
         ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, query) DESC) AS rank_kw
  FROM tool_embeddings
  WHERE tsv @@ plainto_tsquery('english', $query_text)
  LIMIT 20
),
semantic AS (
  SELECT tool_name, provider_name,
         ROW_NUMBER() OVER (ORDER BY embedding <=> $query_vec) AS rank_vec
  FROM tool_embeddings
  ORDER BY embedding <=> $query_vec
  LIMIT 20
)
SELECT COALESCE(k.tool_name, s.tool_name) AS tool_name,
       COALESCE(k.provider_name, s.provider_name) AS provider_name,
       COALESCE(1.0/(60 + k.rank_kw), 0.0)
         + COALESCE(1.0/(60 + s.rank_vec), 0.0) AS rrf_score,
       k.rank_kw AS keyword_rank,
       s.rank_vec AS semantic_rank
FROM keyword k
FULL OUTER JOIN semantic s USING (tool_name)
ORDER BY rrf_score DESC
LIMIT $top_k;
```

### Database Schema

```sql
CREATE TABLE IF NOT EXISTS tool_embeddings (
    embedding_id  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_name     VARCHAR(256) NOT NULL UNIQUE,
    provider_name VARCHAR(64)  NOT NULL,
    embedding     vector(384)  NOT NULL,
    search_text   TEXT         NOT NULL,
    tsv           TSVECTOR,
    model_version VARCHAR(32)  NOT NULL DEFAULT 'all-MiniLM-L6-v2',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- HNSW index for cosine similarity
CREATE INDEX idx_tool_embeddings_hnsw
  ON tool_embeddings USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- GIN index for BM25 full-text search
CREATE INDEX idx_tool_embeddings_tsv
  ON tool_embeddings USING gin (tsv);

-- B-tree indexes
CREATE UNIQUE INDEX idx_tool_embeddings_tool_name ON tool_embeddings (tool_name);
CREATE INDEX idx_tool_embeddings_provider ON tool_embeddings (provider_name);

-- Auto-generate tsvector trigger
CREATE OR REPLACE FUNCTION tool_embeddings_tsv_trigger()
RETURNS TRIGGER AS $$
BEGIN
    NEW.tsv := to_tsvector('english', COALESCE(NEW.search_text, ''));
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tool_embeddings_tsv
    BEFORE INSERT OR UPDATE OF search_text
    ON tool_embeddings
    FOR EACH ROW
    EXECUTE FUNCTION tool_embeddings_tsv_trigger();
```

---

## Data Flow

### On catalog refresh (background, non-blocking):
```
ToolCatalog.refresh()
  -> self._on_refresh_callback(tools)
  -> ToolEmbeddingAdapter.sync_tool_embeddings(tools)
    -> For each tool:
        build_tool_search_text(tool) -> structured text
        EmbeddingAdapter.embed(text) -> 384-dim vector (reuse existing ONNX)
    -> UPSERT into tool_embeddings (ON CONFLICT tool_name DO UPDATE)
```

### On plan generation (request path):
```
PlannerService.generate_plan(intent)
  |-- Fetch tools: catalog.get_user_tools(user_id) -> raise NoToolsConnectedError if empty
  |
  |-- ToolDiscoveryService.discover_tools(intent, tools, entities)
  |   |-- Tier 1A: VectorIndexService.search(intent, top_k=10)
  |   |     -> plan_ids -> load canonical_json -> extract step.uses -> top 10 tools by frequency
  |   |-- Tier 1B: ToolEmbeddingAdapter.search_by_intent(intent, top_k=10)
  |   |     -> top 10 tool_names + rrf_scores
  |   |-- Validate: check Tier 1A high-confidence tools against available_tools
  |   |     -> if missing -> raise ToolNotConnectedError(missing_tools=[...])
  |   |-- Merge: union(1A, 1B) intersect available_tools -> up to ~20 candidates
  |   +-- Tier 2: CrossEncoderReranker.rerank(intent, candidates, top_k=5) -> top 5
  |
  |-- compact_tool_schemas(tools)  [unchanged]
  |-- PromptBuilder -> LLM -> plan
  |
  +-- _finalize_plan(plan)
      |-- resolve_tool() for each step
      +-- If unresolved -> Tier 3: agentic_expand() -> substitute -> validate
```

---

## Component Mapping

- Target: `components/Planner/` (new adapters + domain models)
- Extends: `components/VectorIndex/` (reuses embedding adapter, search patterns)

### New Files

| File | Purpose |
|------|---------|
| `components/Planner/adapters/tool_discovery.py` | `ToolDiscoveryService` — orchestrates 3 tiers |
| `components/Planner/adapters/tool_embedding_adapter.py` | `ToolEmbeddingAdapter` — tool_embeddings table CRUD |
| `components/Planner/adapters/cross_encoder_reranker.py` | `CrossEncoderReranker` — ONNX cross-encoder |
| `components/Planner/domain/tool_discovery_models.py` | `ToolDiscoveryResult`, `ToolEmbeddingResult`, `ToolNotConnectedError` |
| `migrations/015_create_tool_embeddings_table.sql` | DDL + indexes + trigger |
| `components/Planner/tests/test_tool_discovery.py` | Unit tests for all 3 tiers |

### Modified Files

| File | Change |
|------|--------|
| `shared/database/models.py` | Add `ToolEmbeddingTable` (after `PlanEmbeddingTable`, ~line 200) |
| `components/Planner/service/planner_service.py` | Accept `tool_discovery` param; replace filter chain; add Tier 3 in `_finalize_plan`; remove all `get_all_tools()` fallbacks — replace with `get_user_tools()` only + `NoToolsConnectedError` when empty |
| `shared/mcp/catalog.py` | Add `_on_refresh_callback` + `set_refresh_callback()` setter; fire after tool refresh |
| `shared/app.py` | Wire `vector_index_service` into planner factory; set catalog refresh callback |
| `shared/api/orchestrate_routes.py` | Add 422 error handlers for `ToolNotConnectedError` / `NoToolsConnectedError` |
| `Dockerfile` | Add cross-encoder ONNX model download |
| `.env.example` | Add `CROSS_ENCODER_MODEL_PATH`, `TOOL_DISCOVERY_*` vars |
| `components/Planner/tests/conftest.py` | Add `mock_tool_discovery` fixture |

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CROSS_ENCODER_MODEL_PATH` | `~/.cache/vectorindex/cross_encoder.onnx` | Cross-encoder ONNX model path |
| `TOOL_DISCOVERY_ENABLED` | `true` | Master switch; `false` -> legacy keyword filter |
| `TOOL_DISCOVERY_MAX_CANDIDATES` | `20` | Max merged Tier 1 output |
| `TOOL_DISCOVERY_MAX_RERANKED` | `5` | Max Tier 2 output (final tools sent to LLM) |
| `TOOL_DISCOVERY_MIN_THRESHOLD` | `3` | Below this -> fail-open to full catalog |
| `TOOL_DISCOVERY_PLAN_SEARCH_K` | `10` | Top-K plans for Tier 1A (extracts tools from up to 10 matching plans) |
| `TOOL_DISCOVERY_TOOL_SEARCH_K` | `10` | Top-K tool embeddings for Tier 1B |

---

## Graceful Degradation Matrix

| Failure Scenario | Behavior | Tools in Prompt |
|------------------|----------|-----------------|
| VectorIndex available, normal intent | Tier 1 (10+10) -> Tier 2 | 5 (reranked) |
| Novel intent, no plan history | Tier 1B only (10) -> Tier 2 | 5 (reranked from tool embeddings) |
| Insufficient candidates (<3), no missing tools | Fail-open | Full user catalog |
| Plan-proven tool not connected by user | `ToolNotConnectedError` raised (422) | N/A (request fails with actionable error) |
| User has zero connected tools | `NoToolsConnectedError` raised (422) | N/A (request fails, no `get_all_tools()` fallback) |
| VectorIndex unavailable | Keyword fallback | ~8 (existing behavior) |
| Cross-encoder model missing | Tier 1 results passed through (skip Tier 2) | up to ~20 (unranked) |
| `discover_tools()` throws (non-ToolNotConnectedError) | Keyword fallback | ~8 (existing behavior) |
| Plan resolution fails (unknown tool) | Tier 3 agentic expand | Expanded set, re-attempt |
| All tiers fail | Keyword fallback | ~8 (existing behavior) |
| `TOOL_DISCOVERY_ENABLED=false` | Keyword filter only | ~8 (existing behavior) |

---

## Dependencies & Risks

- **cross-encoder/ms-marco-MiniLM-L-6-v2 ONNX model** (~80MB): Downloaded at Docker build time. Risk: HuggingFace model URL could change. Mitigation: pin to a specific commit hash.
- **onnxruntime**: Already a dependency (used by VectorIndex). No new dependency.
- **tokenizers**: Already a dependency. The cross-encoder uses a BERT tokenizer, same library.
- **pgvector extension**: Already required by VectorIndex. No new dependency.
- **Docker image size**: Grows by ~80MB (cross-encoder model). Acceptable given existing bi-encoder is ~23MB.
- **Cold start**: First `sync_tool_embeddings` after catalog refresh takes ~1-2s for ~200 tools. Mitigated: runs asynchronously via callback, does not block request path.
- **tool_embeddings staleness**: New tools added between syncs won't be found by Tier 1B. Mitigated: (a) sync runs on every catalog refresh (hourly), (b) fail-open threshold, (c) Tier 3 agentic fallback searches live catalog.
- **Cross-encoder / bi-encoder alignment**: Different model families but both MiniLM-based. Cross-encoder corrects bi-encoder recall errors — this is the standard retrieval-then-rerank pattern.

---

## Non-Functional Requirements

- Inherit baseline: Preview p95 < 800ms; Execute p95 < 2s; structured logs; no secrets/PII.
- **Tool embedding sync**: p95 < 2s for 200 tools (batch ONNX embed + batch upsert).
- **Tier 1 retrieval** (plan search + tool search): p95 < 60ms (two parallel hybrid SQL queries).
- **Tier 2 reranking** (cross-encoder): p95 < 50ms for 50 candidates on CPU.
- **Total discovery latency** (Tier 1 + Tier 2): p95 < 120ms, well within the plan generation budget.
- **Tier 3 agentic expand**: p95 < 30ms (single hybrid SQL query).
- **Observability**: Structured logging for discovery operations. Log `intent`, `tier`, `candidate_count`, `reranked_count`, `plan_based_tools`, `direct_tools`, `discovery_ms`. Never log raw embeddings.
- **Operational cost**: $0. All inference is local ONNX.

---

## Implementation Sequencing

| Phase | Scope | Behavior Change |
|-------|-------|-----------------|
| 1 | Migration SQL + `ToolEmbeddingTable` in models.py | None (schema only) |
| 2 | `tool_discovery_models.py` (domain dataclasses) | None |
| 3 | `tool_embedding_adapter.py` (embed, store, search) | None |
| 4 | `cross_encoder_reranker.py` (ONNX reranker) | None |
| 5 | `tool_discovery.py` (orchestrator, all 3 tiers) | None |
| 6 | Wire into `planner_service.py` + `app.py` + `catalog.py` | **Active** (behind `TOOL_DISCOVERY_ENABLED`) |
| 7 | Dockerfile + .env.example updates | None |
| 8 | Tests | None |

Each phase is independently shippable. Phases 1-5 add code with no behavior change. Phase 6 activates the new path. Existing `tool_filter.py` is NOT deleted.

---

## Conformance

This work conforms to docs/architecture/GLOBAL_SPEC.md v1.
