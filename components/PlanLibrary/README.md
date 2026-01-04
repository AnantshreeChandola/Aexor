# PlanLibrary Component

Memory Layer component for storing and retrieving executed plans with outcomes. Supports plan pattern learning, similarity search, and performance analytics.

## Overview

PlanLibrary is a core Memory Layer component that:

- **Stores executed plans** with Ed25519 signatures, outcomes, and performance metrics
- **Enables plan pattern learning** through success rate analysis and similarity search
- **Provides Evidence Items** for ContextRAG integration
- **Supports semantic similarity search** using OpenAI embeddings and pgvector
- **Tracks performance analytics** for system optimization

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                 PlanLibrary                         │
│                (Memory Layer)                       │
├─────────────────────────────────────────────────────┤
│ API Layer: FastAPI endpoints                       │
├─────────────────────────────────────────────────────┤
│ Service Layer: Business logic                      │
│  ├── PlanService (storage, canonicalization)       │
│  ├── VectorService (embedding, similarity search)  │
│  ├── AnalyticsService (success rates, patterns)    │
│  └── EvidenceService (Evidence Item conversion)    │
├─────────────────────────────────────────────────────┤
│ Adapter Layer: External integrations               │
│  ├── DatabaseAdapter (SQLAlchemy async)            │
│  ├── VectorAdapter (pgvector operations)           │
│  ├── EmbeddingClient (OpenAI API)                  │
│  └── SignatureVerifier (Ed25519)                   │
├─────────────────────────────────────────────────────┤
│ Data Layer: PostgreSQL 16 + pgvector + Redis      │
└─────────────────────────────────────────────────────┘
```

## Dependencies

### External Services
- **PostgreSQL 16** with pgvector extension
- **OpenAI API** for text embeddings (text-embedding-ada-002)
- **Redis 7** for optional caching

### Python Dependencies
All dependencies are included in the project's `pyproject.toml`:
- `sqlalchemy[asyncio]>=2.0` - Database ORM
- `pgvector>=0.2.4` - Vector operations
- `openai>=1.10.0` - Embedding generation  
- `cryptography>=41.0` - Signature verification
- `ulid-py>=1.1.0` - ULID validation
- `fastapi>=0.109.0` - API framework

## Database Schema

The component uses these tables (defined in `shared/database/models.py`):

- **`plans`** - Plan storage with signatures and metadata
- **`plan_outcomes`** - Execution results and success tracking
- **`plan_embeddings`** - Vector embeddings for similarity search
- **`plan_metrics`** - Performance metrics and timing data

## API Endpoints

### Plan Storage
```http
POST /api/v1/plans
```
Store executed plan with outcome and metrics.

### Plan Querying
```http
GET /api/v1/plans/by-intent?intent_type=schedule_meeting&success_threshold=0.7
```
Query plans by intent type with success filtering.

```http
GET /api/v1/plans/similarity?query_text=book+restaurant&similarity_threshold=0.6
```
Find similar plans using semantic search.

```http
GET /api/v1/plans/{plan_id}
```
Retrieve specific plan by ID.

### Analytics
```http
GET /api/v1/plans/analytics/success-rates?timeframe_days=30
```
Get success rate analytics by intent type.

### Health Check
```http
GET /api/v1/plans/health
```
Component health status and dependency checks.

## Usage Examples

### Store a Plan
```python
from components.PlanLibrary.domain.models import (
    Plan, PlanIntentModel, PlanStepModel, PlanMetaModel,
    Signature, PlanOutcome, PlanMetrics, StorePlanRequest
)

# Create plan
plan = Plan(
    plan_id="01HX0123456789ABCDEFGHIJK",
    intent=PlanIntentModel(type="schedule_meeting"),
    graph=[
        PlanStepModel(step_id="step_1", operation="fetch_calendar")
    ],
    meta=PlanMetaModel(created_at=datetime.now(timezone.utc))
)

# Create outcome and metrics
outcome = PlanOutcome(
    plan_id=plan.plan_id,
    success=True,
    execution_start=datetime.now(timezone.utc),
    execution_end=datetime.now(timezone.utc),
    total_steps=1
)

metrics = PlanMetrics(
    plan_id=plan.plan_id,
    execute_latency_ms=1200
)

# Store via API
request = StorePlanRequest(
    plan=plan,
    signature=signature,
    outcome=outcome,
    metrics=metrics
)
```

### Query Plans by Intent
```python
from components.PlanLibrary.service.plan_service import PlanService

plan_service = PlanService(db_adapter, signature_verifier, vector_service)

# Get successful meeting scheduling patterns
patterns = await plan_service.get_plans_by_intent(
    intent_type="schedule_meeting",
    success_threshold=0.8,
    limit=10
)

# Convert to Evidence Items for ContextRAG
evidence_items = [pattern.to_evidence_item() for pattern in patterns]
```

### Similarity Search
```python
from components.PlanLibrary.service.vector_service import VectorService

vector_service = VectorService(embedding_client, vector_adapter)

# Find similar plans
matches = await vector_service.similarity_search(
    query_text="book a restaurant for dinner",
    similarity_threshold=0.6,
    limit=5
)

# Convert to Evidence Items
evidence_items = [match.to_evidence_item() for match in matches]
```

## Evidence Item Integration

PlanLibrary returns data as Evidence Items for ContextRAG integration:

```json
{
  "type": "plan",
  "key": "schedule_meeting_pattern_123",
  "value": {
    "intent": "schedule_meeting",
    "success_rate": 0.85,
    "avg_execution_time_ms": 1200,
    "steps_count": 6,
    "pattern_summary": "Fetch calendars → Find overlap → User choice → Book event",
    "total_executions": 20,
    "last_execution": "2025-01-03T10:30:00Z"
  },
  "confidence": 0.85,
  "source_ref": "planlibrary:plans/01HX123456",
  "ttl_days": null,
  "tier": 3
}
```

## Configuration

Environment variables:
```bash
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/db
OPENAI_API_KEY=your_openai_api_key
REDIS_URL=redis://localhost:6379
```

## Testing

Run tests:
```bash
# Unit and contract tests
pytest components/PlanLibrary/tests/

# Integration tests (requires database)
pytest components/PlanLibrary/tests/ --run-integration

# Specific test files
pytest components/PlanLibrary/tests/test_schemas.py -v
pytest components/PlanLibrary/tests/test_contract.py -v
```

## Performance Targets

- **Vector similarity search**: p95 < 100ms (GLOBAL_SPEC requirement)
- **Plan storage**: p95 < 200ms 
- **Intent-based queries**: p95 < 150ms
- **Support**: 100,000+ plans with linear query performance

## Safety Features

- **Circuit breaker** for OpenAI API failures
- **Graceful degradation** when embeddings unavailable
- **Signature verification** for plan integrity
- **Background embedding generation** with retry logic
- **Fault isolation** prevents cascade failures

## Development

The component follows the implementer agent methodology:
- **Preview-first safety** patterns (though not applicable for internal component)
- **Idempotency** via plan_id uniqueness
- **DRY architecture** using shared infrastructure
- **Component-first** design with clear boundaries
- **Test-driven development** with comprehensive coverage

For more details, see the component's LLD.md and task breakdown in tasks.md.