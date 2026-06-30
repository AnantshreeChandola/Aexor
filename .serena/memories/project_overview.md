# Aexor - Project Overview

<p align="center">
  <img src="../../static/aexor-logo-top-cropped.png" alt="Aexor Logo" width="200">
</p>

## Purpose
Aexor is a preview-first personal assistant with deterministic planning and adaptive execution. It produces immutable plan DAGs where LLM Reasoner steps can spawn bounded new steps at runtime, creating versioned revisions with PolicyEngine attestations. All execution runs through a pure Python/FastAPI runtime with MCP tool invocations — no external workflow engines.

## Tech Stack
- **Python 3.11+** with type hints
- **FastAPI** for async HTTP
- **Pydantic v2** for data validation
- **SQLAlchemy 2.0** with asyncpg
- **Redis 7** with hiredis for caching and coordination
- **PostgreSQL 16** with pgvector extension
- **Anthropic Claude API** (Sonnet) for planning, intent parsing, and runtime reasoning
- **OpenAI API** (optional fallback) for plan generation
- **ONNX Runtime** (local, all-MiniLM-L6-v2) for embeddings (384-dim, zero API cost)
- **MCP protocol** for all external tool invocations (Composio, custom servers)

## Architecture — 4 Layers, 15 Components

**Layer 1 — Memory & Persistence** (all implemented)
| Component | Purpose |
|-----------|---------|
| ProfileStore | Stable user preferences and consent settings |
| History | Normalized, PII-light facts about past actions (30-day TTL) |
| PlanLibrary | All past plans with outcomes |
| VectorIndex | Hybrid BM25 + semantic search (pgvector + ONNX Runtime) |

**Layer 2 — Domain Services** (all implemented)
| Component | Purpose |
|-----------|---------|
| Intake | Multi-turn intent collection with LLM parsing and Redis sessions |
| ContextRAG | Tiered evidence gathering from Memory Layer (≤2KB budget) |
| Planner | Deterministic plan generation via Anthropic Claude API with 4-level fallback |
| PolicyEngine | Policy rule evaluation, attestations, HITL enforcement |
| PluginRegistry | Tool catalog with scope verification and credential resolution |
| PlanWriter | Outcome persistence with fact derivation |

**Layer 3 — Orchestration** (all implemented)
| Component | Purpose |
|-----------|---------|
| PreviewOrchestrator | Side-effect-free plan preview with dry-run MCP and Redis cache |
| ApprovalGate | HITL approval workflow with JWT tokens, single-use, multi-gate support |
| ExecuteOrchestrator | Pure agentic DAG execution — MCP dispatch, two-tier LLM, spawning |
| ExecutionMonitor | Stuck execution detection and timeout enforcement |

**Layer 4 — Platform** (all implemented)
| Component | Purpose |
|-----------|---------|
| Audit | Append-only audit trail for all plan/execution/approval events |
| IntegrationManager | User-provider connection status, Composio OAuth, per-user MCP tools |

## Architecture Principles
- **Preview-first safety** — never execute without showing the user first
- **Deterministic planning with adaptive execution** — initial plan (revision 0) is immutable; Reasoner steps may spawn bounded new steps at runtime
- **Pure agentic runtime** — Python ExecuteOrchestrator dispatches all steps via MCP (APIs) and Anthropic API (reasoning)
- **Two-tier LLM execution** — sandboxed Tier 1 (untrusted external data, no tools) + capable Tier 2 (agent reasoning, MCP tools)
- **PolicyEngine governance** — deny-by-default; actions are only allowed when an explicit policy rule matches
- **HITL approval gates** — JWT tokens, Redis gate state, single-use enforcement
- **Seven runtime roles** — Fetcher, Analyzer, Watcher, Resolver, Booker, Notifier, Reasoner

## LLM Models

| Purpose | Default Model | Env Var |
|---------|--------------|---------|
| Planning (primary) | `claude-sonnet-4-5-20250929` | `PLANNER_PRIMARY_MODEL` |
| Planning (fallback) | `claude-sonnet-4-5-20250929` | `PLANNER_FALLBACK_MODEL` |
| Intent parsing | `claude-sonnet-4-5-20250929` | `INTAKE_PARSER_MODEL` |
| Runtime reasoning | Per-step via `reasoning_config.model` | Set in plan by Planner |
| Embeddings | `all-MiniLM-L6-v2` (ONNX, local) | Bundled, no API |

## Key Locations
- `components/` — Self-contained component packages (SPEC.md, LLD.md, schemas/, tests/, code)
- `shared/` — Cross-component infrastructure (schemas, database, middleware, DI wiring)
- `docs/architecture/` — GLOBAL_SPEC, Project_HLD, QUICK_REFERENCE
- `docker-compose.yml` — Full local stack (PostgreSQL, Redis, app)
- `.claude/` — Claude Code configuration
- `.specify/` — Git Spec Kit workspace
