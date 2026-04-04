# Personal Agent - Project Overview

## Purpose
A preview-first personal assistant system with deterministic planning and adaptive execution (fixed plan DAGs + LLM Reasoner steps), policy-bounded execution, and component-first architecture. The system uses asynchronous runtime agents for responsibility isolation, enabling parallel task execution across calendars, shopping, travel, and more.

## Tech Stack
- **Python 3.11+** with type hints
- **FastAPI** for async HTTP
- **Pydantic v2** for data validation
- **SQLAlchemy 2.0** with asyncpg
- **aioredis** for async Redis
- **PostgreSQL 16** with pgvector extension
- **Redis 7** for caching and coordination
- **Anthropic Claude API** (Sonnet 4/Opus) for planning and runtime reasoning
- **ONNX Runtime** (local, all-MiniLM-L6-v2) for embeddings (384-dim)
- **Python/FastAPI ExecuteOrchestrator** with MCP protocol for all tool invocations

## Architecture Principles
- **Preview-first safety model** - all operations preview before execution
- **Deterministic planning with adaptive execution** - fixed plan DAG (same inputs → same graph → same signature), LLM Reasoner steps adapt at runtime within PolicyEngine bounds
- **Component-first architecture** with self-contained packets
- **Seven runtime agent roles**: Fetcher, Analyzer, Watcher, Resolver, Booker, Notifier, Reasoner
- **Pure agentic runtime**: Python ExecuteOrchestrator dispatches all steps via MCP tool invocations
- **PolicyEngine** - evaluates policy rules, issues attestations, enforces HITL for critical actions
- **Ed25519 signatures** for plan integrity (+ PolicyEngine attestations for runtime modifications)
- **Human-in-the-loop gates** for approval workflows

## Key Locations
- `components/` - Self-contained component packets (SPEC.md, LLD.md, schemas/, tests/, code)
- `usecases/` - End-to-end use case definitions
- `shared/` - Cross-component utilities and schemas
- `docs/architecture/` - GLOBAL_SPEC, HLD, ADRs
- `.claude/` - Claude Code configuration (commands, agents, skills)
- `.specify/` - Git Spec Kit workspace