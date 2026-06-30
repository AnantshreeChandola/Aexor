# Research: Intake

**Date**: 2026-03-26

## R1: Intent Taxonomy — Open vs Fixed

**Decision**: Open taxonomy. Intake accepts any intent string.
**Rationale**: Tool availability is validated downstream by Planner (`PlanValidationError "Unknown tools"`) and PluginRegistry (`ToolNotFoundError → 404`). Both components already handle this. Gatekeeping at Intake would create unnecessary coupling and require Intake to know about all available tools.
**Alternatives considered**: Fixed intent enum — rejected because it requires Intake updates every time a new tool is registered.

## R2: Intent Parsing Strategy — Rules-based vs LLM

**Decision**: Rules-based parser (MVP) behind `IntentParser` protocol.
**Rationale**: No API cost, deterministic, fast (<1ms). Protocol allows swapping to LLM-based parser later without changing service layer. MVP accuracy is acceptable — complex parsing deferred to LLM future.
**Alternatives considered**: LLM-based parser (Anthropic Claude) — deferred to v2 due to latency (~500ms), cost, and complexity.

## R3: Readiness Detection — Auto vs Manual

**Decision**: Auto-readiness heuristic (intent + ≥1 entity). No "submit" button.
**Rationale**: Better UX — user doesn't need to explicitly confirm. Heuristic is behind `ReadinessChecker` protocol for extensibility. If user wants to start over, they create a new session.
**Alternatives considered**: Manual submit — rejected for worse UX; explicit button adds friction.

## R4: Session Storage — Redis vs PostgreSQL

**Decision**: Redis with JSON serialization, `session:{user_id}:{session_id}` key, 1h TTL.
**Rationale**: Sessions are ephemeral (max 1h). Redis provides natural TTL expiry, fast reads (<1ms), and is already in the tech stack per MODULAR_ARCHITECTURE §3 Redis Key Patterns. PostgreSQL would add unnecessary persistence for throwaway data.
**Alternatives considered**: PostgreSQL `sessions` table — rejected because sessions are ephemeral and don't need durability. MODULAR_ARCHITECTURE Table Ownership Map marks Intake's sessions table as "(Optional - if not Redis)".

## R5: Follow-up Prompts — Static vs LLM-generated

**Decision**: Static templates (MVP).
**Rationale**: Deterministic, no API cost, sufficient for MVP. Templates keyed by detected intent type with generic fallback.
**Alternatives considered**: LLM-generated prompts — deferred to v2 (same protocol extensibility as parser).

## R6: Timezone Source

**Decision**: Client header `X-Timezone` with fallback to `America/Chicago`.
**Rationale**: Simple, no dependency on ProfileStore. Client knows user's timezone. Fallback matches Intent model default.
**Alternatives considered**: ProfileStore lookup — adds component dependency for a simple value; may be added later.

## R7: Session ID Format

**Decision**: `ses_<26-char ULID>` (e.g., `ses_01JXYZ...`).
**Rationale**: ULID provides sortable, globally unique IDs. `ses_` prefix makes IDs self-documenting. Consistent with existing project patterns (plan_id uses ULID).
**Alternatives considered**: UUID4 — rejected because ULIDs are sortable by creation time.

## R8: Redis Client Library

**Decision**: `redis[hiredis]>=5.0` (already in pyproject.toml).
**Rationale**: Already a project dependency. Async support via `redis.asyncio`. hiredis provides C-based parser for better performance.
**Alternatives considered**: aioredis — deprecated; merged into redis-py 5.0+.

## R9: Session Ownership Enforcement

**Decision**: Redis key pattern `session:{user_id}:{session_id}` inherently scopes sessions to users. User A cannot access User B's session because the key includes user_id from the JWT.
**Rationale**: No separate ownership check needed at the application layer — the Redis key structure enforces isolation by design.
**Alternatives considered**: Separate ownership field in session JSON — redundant given key design.
