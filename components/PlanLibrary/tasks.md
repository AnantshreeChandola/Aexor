# Tasks: PlanLibrary

**Created**: 2025-01-03
**Branch**: feat/planlibrary-implementation
**SPEC**: specs/004-feature-title-planlibrary/spec.md
**LLD**: components/PlanLibrary/LLD.md

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture.

---

## Phase 0: Setup & Dependencies

### Install Dependencies (from LLD.md Section 7)
- [ ] [T000] Install Python packages from LLD dependencies section
  - sqlalchemy>=2.0,<3.0 (Async ORM for PostgreSQL)
  - asyncpg>=0.28.0 (High-performance PostgreSQL adapter)
  - pydantic>=2.0,<3.0 (Data validation and schema compliance)
  - fastapi>=0.104.0 (API framework with async support)
  - pgvector>=0.2.0 (PostgreSQL vector extension support)
  - openai>=1.0.0 (Embedding API client)
  - redis>=4.5.0 (Caching and rate limiting)
  - cryptography>=41.0.0 (Ed25519 signature verification)
  - ulid-py>=1.1.0 (ULID validation and generation)
- [ ] [T001] Verify PostgreSQL 16 with pgvector extension is available
- [ ] [T002] Set up Redis connection for caching (optional, with graceful degradation)
- [ ] [T003] Configure OpenAI API access for embedding generation

---

## Phase 1: Schemas & Domain (Foundation)

### Acceptance Criterion: AC-001 - Store Plan Execution Results (Priority P1)

- [ ] [T100] Create response.normalized.json schema (components/PlanLibrary/schemas/)
  - StorePlanRequest schema with plan, signature, outcome, metrics
  - StorePlanResponse schema with success status and plan_id
  - Error response schemas for all error codes from SPEC
- [ ] [T101] Create domain models (components/PlanLibrary/domain/)
  - Plan entity with canonical JSON storage
  - PlanOutcome entity with success tracking and error details
  - PlanEmbedding entity with 1536-dimension vector storage
  - PlanMetrics entity with performance data
  - Evidence Item output model for ContextRAG integration
- [ ] [T102] Write schema validation tests (tests/test_schemas.py)
  - Test all domain models with valid and invalid data
  - Test Evidence Item format compliance with GLOBAL_SPEC
  - Test ULID validation for plan_id fields

---

## Phase 2: Service Layer (Business Logic)

### Acceptance Criterion: AC-001 - Store Plan Execution Results & AC-002 - Query Plans by Success Rate

- [ ] [T200] Implement PlanService.store_plan() (components/PlanLibrary/service/plan_service.py)
  - Signature verification using Ed25519
  - Plan canonicalization and hash generation  
  - Database transaction for atomic storage
  - Graceful handling of duplicate plan_id
  - Plan size validation (1MB max, 100 steps max)
- [ ] [T201] Implement PlanService.get_plans_by_intent() (components/PlanLibrary/service/plan_service.py)
  - Query by intent type with success rate filtering
  - Return plans sorted by success rate
  - Support recency filtering and pagination
  - Convert to Evidence Item format
- [ ] [T202] Implement VectorService for embeddings (components/PlanLibrary/service/vector_service.py)
  - Async embedding generation with OpenAI API
  - Circuit breaker pattern for API failures
  - Background queue for embedding retry
  - Similarity search using pgvector cosine similarity
- [ ] [T203] Implement AnalyticsService (components/PlanLibrary/service/analytics_service.py)
  - Calculate success rates by intent type
  - Performance trend analysis
  - High-performing pattern identification
- [ ] [T204] Write service tests (tests/test_service.py)
  - Test plan storage with valid signatures
  - Test signature verification failure cases
  - Test query filtering and sorting logic
  - Test Evidence Item conversion

---

## Phase 3: Adapters (External Integrations)

### Acceptance Criterion: AC-003 - Semantic Similarity Search (Priority P2)

- [ ] [T300] Create DatabaseAdapter (components/PlanLibrary/adapters/db.py)
  - Use shared database infrastructure from DRY architecture
  - Async transaction management for plan storage
  - PostgreSQL table schema for plans, outcomes, embeddings, metrics
  - Connection pooling and retry logic
- [ ] [T301] Create VectorAdapter (components/PlanLibrary/adapters/vector_db.py)
  - pgvector integration for embedding storage
  - HNSW index optimization for similarity search
  - Cosine similarity query implementation
  - Vector search with threshold filtering
- [ ] [T302] Create EmbeddingClient (components/PlanLibrary/adapters/embedding_client.py)
  - OpenAI text-embedding-ada-002 API integration
  - Circuit breaker with 5-minute timeout
  - Exponential backoff retry (3 attempts)
  - Rate limiting and quota management
- [ ] [T303] Create SignatureVerifier (components/PlanLibrary/adapters/signature_verifier.py)
  - Ed25519 signature verification
  - Plan canonicalization for consistent hashing
  - SHA-256 hash generation for integrity
- [ ] [T304] Write adapter tests with mocks (tests/test_adapters.py)
  - Mock OpenAI API calls for embedding generation
  - Test database transaction rollback scenarios
  - Test circuit breaker behavior
  - Test signature verification edge cases

---

## Phase 4: API Handlers (Thin Wrappers)

### Acceptance Criterion: AC-004 - External Contract & Error Handling

- [ ] [T400] Create API handler (components/PlanLibrary/api/routes.py)
  - POST /plans endpoint for plan storage
  - GET /plans/by-intent endpoint for intent queries
  - GET /plans/similarity endpoint for vector search
  - GET /health endpoint for health checks
  - Thin wrappers delegating to service layer
  - Proper error response formatting per SPEC
- [ ] [T401] Write handler tests (tests/test_handler.py)
  - Test all API endpoints with valid requests
  - Test error response format compliance
  - Test request validation and schema conformance
  - Test async endpoint behavior

---

## Phase 5: Fault Isolation & Safety (Architectural)

### From MODULAR_ARCHITECTURE.md and LLD Architectural Considerations

- [ ] [T500] Implement circuit breaker for OpenAI API calls
  - 5-minute timeout with exponential backoff
  - Fallback to storing plans without embeddings
  - Circuit breaker state monitoring and alerts
- [ ] [T501] Add fallback behavior for embedding failures
  - Graceful degradation when vector service unavailable
  - Background retry queue for failed embeddings
  - Similarity search unavailable error handling
- [ ] [T502] Validate determinism: same inputs → same plan storage
  - Canonical JSON serialization (sorted keys)
  - Consistent hash generation for plan integrity
  - Deterministic signature verification
- [ ] [T503] Add structured logging (correlation: plan_id/component/operation)
  - All operations logged with plan_id correlation
  - No PII in logs (sanitized plan summaries only)
  - Performance metrics logging (latency, throughput)
- [ ] [T504] Verify blast radius containment
  - PlanLibrary failure doesn't affect plan execution
  - Graceful degradation preserves system functionality
  - Component isolation from upstream failures

---

## Phase 6: Contract Tests & Integration

### Acceptance Criterion: AC-005 - Evidence Item Integration & Performance Requirements

- [ ] [T600] Write contract tests (tests/test_contract.py)
  - Test Evidence Item format compliance with GLOBAL_SPEC
  - Test plan storage → query → Evidence conversion flow
  - Test similarity search accuracy and performance
  - Test all error codes and response formats from SPEC
- [ ] [T601] Integration test with PostgreSQL and pgvector
  - Full database integration testing
  - Vector similarity search performance testing
  - Transaction rollback and data consistency
  - Connection pooling and error recovery
- [ ] [T602] Performance benchmarks (tests/test_performance.py)
  - Vector similarity search p95 < 100ms (GLOBAL_SPEC)
  - Plan storage p95 < 200ms (GLOBAL_SPEC)
  - Intent-based queries p95 < 150ms
  - Load testing with 1000+ plans
- [ ] [T603] Validate archival strategy implementation
  - Plan size limits enforcement (1MB, 100 steps)
  - Storage growth monitoring and alerting
  - Archive old plans strategy design

---

## Task Summary

- **Total Tasks**: 24
- **Setup**: T000-T003 (4 tasks)
- **Schemas**: T100-T102 (3 tasks)  
- **Service**: T200-T204 (5 tasks)
- **Adapters**: T300-T304 (5 tasks)
- **API**: T400-T401 (2 tasks)
- **Safety**: T500-T504 (5 tasks)
- **Tests**: T600-T603 (4 tasks)

## Dependencies

**External** (from LLD.md Section 7):
- PostgreSQL 16 with pgvector extension for plan storage and similarity search
- OpenAI Embedding API (text-embedding-ada-002) for vector generation  
- Redis 7 for optional caching and rate limiting
- Python packages: SQLAlchemy 2.0+, FastAPI, Pydantic v2, asyncpg, cryptography

**Internal** (from LLD.md Section 7):
- Shared database infrastructure (connection pooling, error handling)
- Shared API infrastructure (authentication, error response patterns)
- Shared schemas (Evidence Item format, Signature format)
- No component dependencies (Memory Layer foundation component)

## Architectural Considerations

**Blast Radius** (from LLD):
- If PlanLibrary fails: Plan execution continues normally, historical context unavailable
- Containment: Circuit breaker on OpenAI API, graceful degradation without embeddings

**Determinism** (from LLD):
- Plan Storage: Canonical JSON serialization ensures consistent hashing
- Query Results: Deterministic ordering by success_rate DESC, total_executions DESC
- Signature Verification: Cryptographic integrity guarantees

**Performance Targets** (from GLOBAL_SPEC and SPEC):
- Vector similarity search: p95 < 100ms
- Plan storage: p95 < 200ms  
- Intent-based queries: p95 < 150ms
- Support 100,000+ plans with linear performance

**Fault Isolation**:
- Memory Layer component with no upstream dependencies
- Circuit breakers prevent API failure cascades
- Database transaction isolation ensures data consistency
- Background embedding generation with retry queue