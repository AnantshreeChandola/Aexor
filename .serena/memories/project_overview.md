# Personal Agent - Project Overview

## Purpose
A preview-first personal assistant system with deterministic planning, multi-agent orchestration, and component-first architecture. The system uses asynchronous runtime agents for responsibility isolation, enabling parallel task execution across calendars, shopping, travel, and more.

## Tech Stack
- **Python 3.11+** with type hints
- **FastAPI** for async HTTP
- **Pydantic v2** for data validation
- **SQLAlchemy 2.0** with asyncpg
- **aioredis** for async Redis
- **PostgreSQL 16** with pgvector extension
- **Redis 7** for caching and coordination
- **Anthropic Claude API** (Sonnet 4/Opus) for planning and reasoning
- **OpenAI API** for embeddings only
- **Temporal** (Python SDK) for long-running durable tasks
- **n8n** (self-hosted) for short interactive workflows

## Architecture Principles
- **Preview-first safety model** - all operations preview before execution
- **Deterministic planning** - same inputs always produce same plan
- **Component-first architecture** with self-contained packets
- **Six runtime agent roles**: Fetcher, Analyzer, Watcher, Resolver, Booker, Notifier
- **Dual orchestration runtime**: n8n for short jobs, Temporal for durable workflows
- **Ed25519 signatures** for plan integrity
- **Human-in-the-loop gates** for approval workflows

## Key Locations
- `components/` - Self-contained component packets (SPEC.md, LLD.md, schemas/, tests/, code)
- `usecases/` - End-to-end use case definitions
- `shared/` - Cross-component utilities and schemas
- `docs/architecture/` - GLOBAL_SPEC, HLD, ADRs
- `.claude/` - Claude Code configuration (commands, agents, skills)
- `.specify/` - Git Spec Kit workspace