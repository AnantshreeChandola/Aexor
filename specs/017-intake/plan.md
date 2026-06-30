# Implementation Plan: Intake

**Branch**: `feat/intake` | **Date**: 2026-03-26 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/017-intake/spec.md`

## Summary

Intake is the system's HTTP entry point (API/Interface Layer). It receives raw user messages via `POST /intake/message`, manages multi-turn conversation state via Redis-backed sessions (`session:{user_id}:{session_id}`, 1h TTL), and emits finalized Intent objects (GLOBAL_SPEC §2.1) when readiness is detected. Rules-based MVP parser behind `IntentParser` protocol; `ReadinessChecker` protocol for extensible readiness heuristic (intent + ≥1 entity). No PostgreSQL tables — Redis-only for ephemeral session state.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: FastAPI, Pydantic v2, redis[hiredis]>=5.0, ulid-py>=1.1.0
**Storage**: Redis 7 (sessions only — no PostgreSQL tables owned)
**Testing**: pytest>=8.0, pytest-asyncio>=0.23, pytest-mock>=3.12
**Target Platform**: Linux server (self-hosted, single-tenant, multi-user)
**Project Type**: Component in component-first architecture (ADR-0001)
**Performance Goals**: Intake p95 < 200ms (message → response)
**Constraints**: Max 10,000 char messages, 20 turns/session, 50KB session state, 1h TTL
**Scale/Scope**: Single-tenant, multi-user (per GLOBAL_SPEC v2.2 deployment model)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| I. Component-first architecture | ✅ PASS | `components/Intake/` with api/, service/, domain/, adapters/, tests/ |
| II. Preview-first safety | ✅ N/A | Intake is internal — safety model does not apply (GLOBAL_SPEC §1) |
| III. Test-first development | ✅ PLANNED | Tests: test_unit, test_service, test_contract, test_observability |
| IV. Schema validation | ✅ PASS | Uses existing `shared/schemas/intent.py` for Intent output |
| V. Deterministic planning | ✅ N/A | Intake does not produce plans |
| VI. Observability & Privacy | ✅ PLANNED | Structured logging, no PII (FR-010), correlation by session_id |
| VII. Fault isolation | ✅ PLANNED | Redis unavailability → 503; single external dep, no circuit breaker needed |

## Project Structure

### Documentation (this feature)

```text
specs/017-intake/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── contracts/
    └── intake-api.md    # API contract spec
```

### Source Code (repository root)

```text
components/Intake/
├── __init__.py
├── api/
│   ├── __init__.py
│   └── routes.py            # POST /message, DELETE /session/{session_id}, GET /health
├── service/
│   ├── __init__.py
│   └── intake_service.py    # IntakeService + create_intake_service() factory
├── domain/
│   ├── __init__.py
│   └── models.py            # Session, IntakeMessage, IntakeResponse, errors
├── adapters/
│   ├── __init__.py
│   ├── session_store.py     # SessionStore protocol + RedisSessionStore
│   ├── intent_parser.py     # IntentParser protocol + RulesBasedParser
│   └── readiness_checker.py # ReadinessChecker protocol + RulesBasedReadinessChecker
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_unit.py         # Parser, readiness, session store
│   ├── test_service.py      # IntakeService integration
│   ├── test_contract.py     # Intent §2.1 conformance
│   └── test_observability.py # No PII in logs
├── LLD.md
└── diagrams/
    └── flow.md
```

**Shared file changes:**
- `shared/app.py` — Add IntakeService to lifespan DI (Redis client + create_intake_service)
- `shared/dependencies.py` — Add `get_intake_service()`

**Structure Decision**: Standard component-first layout per ADR-0001.

## Complexity Tracking

No constitution violations. All gates pass or are N/A.
