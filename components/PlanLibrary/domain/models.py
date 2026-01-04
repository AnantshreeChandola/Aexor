"""
Domain Models for PlanLibrary

Pydantic models for plans, outcomes, metrics, and database entities.
All models follow the schema definitions and GLOBAL_SPEC requirements.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator
from shared.schemas.evidence import EvidenceItem


class PlanIntentModel(BaseModel):
    """Plan intent representation."""
    type: str = Field(..., min_length=1, max_length=64)
    description: Optional[str] = Field(None, max_length=512)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class PlanStepModel(BaseModel):
    """Individual plan execution step."""
    step_id: str = Field(..., min_length=1, max_length=64)
    operation: str = Field(..., min_length=1, max_length=128)
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)


class PlanMetaModel(BaseModel):
    """Plan metadata information."""
    created_at: datetime
    version: str = Field(default="1.0", max_length=16)
    creator: Optional[str] = Field(None, max_length=64)


class Plan(BaseModel):
    """
    Complete execution plan model.
    
    Represents a plan with all its components including intent,
    execution graph, constraints, and metadata.
    """
    plan_id: str = Field(..., pattern=r'^[0-9A-HJKMNP-TV-Z]{26}$')
    intent: PlanIntentModel
    graph: List[PlanStepModel] = Field(..., max_length=100, min_length=1)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    meta: PlanMetaModel

    @field_validator('plan_id')
    @classmethod
    def validate_plan_id(cls, v: str) -> str:
        """Validate plan_id is a valid ULID."""
        if not v or len(v) != 26:
            raise ValueError("plan_id must be a 26-character ULID")
        return v

    def to_canonical_json(self) -> str:
        """
        Convert plan to canonical JSON representation.
        
        Returns deterministic JSON with sorted keys for consistent hashing.
        """
        plan_dict = self.model_dump(mode='json')  # Use JSON mode to handle datetime serialization
        return json.dumps(plan_dict, sort_keys=True, separators=(',', ':'))

    def get_plan_hash(self) -> str:
        """
        Generate SHA-256 hash of canonical plan JSON.
        
        Returns:
            Hex string of plan hash for integrity verification
        """
        import hashlib
        canonical_json = self.to_canonical_json()
        return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()

    def get_size_bytes(self) -> int:
        """Calculate plan size in bytes."""
        return len(self.to_canonical_json().encode('utf-8'))

    @field_validator('graph')
    @classmethod
    def validate_graph_size(cls, v: List[PlanStepModel]) -> List[PlanStepModel]:
        """Validate graph doesn't exceed step limits."""
        if len(v) > 100:
            raise ValueError("Plan cannot exceed 100 steps")
        return v


class Signature(BaseModel):
    """Ed25519 signature for plan verification."""
    signature: str = Field(..., min_length=1)
    public_key: str = Field(..., min_length=1) 
    algorithm: str = Field(default="Ed25519", pattern=r'^Ed25519$')
    signed_at: Optional[datetime] = Field(default=None)

    @field_validator('algorithm')
    @classmethod
    def validate_algorithm(cls, v: str) -> str:
        """Ensure only Ed25519 signatures are accepted."""
        if v != "Ed25519":
            raise ValueError("Only Ed25519 signatures are supported")
        return v


class PlanOutcome(BaseModel):
    """Plan execution outcome with success/failure tracking."""
    outcome_id: UUID = Field(default_factory=uuid4)
    plan_id: str = Field(..., pattern=r'^[0-9A-HJKMNP-TV-Z]{26}$')
    success: bool
    error_type: Optional[str] = Field(None, max_length=64)
    error_details: Optional[Dict[str, Any]] = Field(None)
    execution_start: datetime
    execution_end: datetime
    total_steps: int = Field(..., ge=0)
    failed_step: Optional[int] = Field(None, ge=0)
    context_data: Optional[Dict[str, Any]] = Field(None)

    @field_validator('failed_step')
    @classmethod
    def validate_failed_step(cls, v: Optional[int], info) -> Optional[int]:
        """Validate failed_step is within total_steps if provided."""
        if v is not None and 'total_steps' in info.data:
            total_steps = info.data['total_steps']
            if v >= total_steps:
                raise ValueError(f"failed_step ({v}) cannot exceed total_steps ({total_steps})")
        return v

    def get_duration_seconds(self) -> float:
        """Calculate execution duration in seconds."""
        delta = self.execution_end - self.execution_start
        return delta.total_seconds()


class StepTiming(BaseModel):
    """Timing data for individual plan step."""
    step_id: str = Field(..., min_length=1, max_length=64)
    duration_ms: int = Field(..., ge=0)


class ResourceUsage(BaseModel):
    """Resource utilization metrics."""
    memory_mb: Optional[float] = Field(None, ge=0)
    cpu_percent: Optional[float] = Field(None, ge=0, le=100)


class PlanMetrics(BaseModel):
    """Plan execution performance metrics."""
    metrics_id: UUID = Field(default_factory=uuid4)
    plan_id: str = Field(..., pattern=r'^[0-9A-HJKMNP-TV-Z]{26}$')
    preview_latency_ms: Optional[int] = Field(None, ge=0)
    execute_latency_ms: int = Field(..., ge=0)
    step_timings: List[StepTiming] = Field(default_factory=list)
    resource_usage: Optional[ResourceUsage] = Field(None)


class PlanEmbedding(BaseModel):
    """Vector embedding for plan similarity search."""
    embedding_id: UUID = Field(default_factory=uuid4)
    plan_id: str = Field(..., pattern=r'^[0-9A-HJKMNP-TV-Z]{26}$')
    vector: List[float] = Field(..., min_length=1536, max_length=1536)
    model_version: str = Field(default="text-embedding-ada-002")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    vector_norm: Optional[float] = Field(None, ge=0)

    @field_validator('vector')
    @classmethod
    def validate_vector_dimensions(cls, v: List[float]) -> List[float]:
        """Ensure vector has exactly 1536 dimensions for OpenAI ada-002."""
        if len(v) != 1536:
            raise ValueError(f"Vector must have exactly 1536 dimensions, got {len(v)}")
        return v

    @model_validator(mode='after')
    def calculate_vector_norm(self):
        """Calculate and store vector norm for efficiency."""
        if self.vector and self.vector_norm is None:
            import math
            self.vector_norm = math.sqrt(sum(x * x for x in self.vector))
        return self


# Database models for SQLAlchemy
class PlanDB(BaseModel):
    """Database model for stored plans."""
    plan_id: str
    canonical_json: Dict[str, Any]
    signature_data: Dict[str, Any] 
    intent_type: str
    step_count: int
    plan_hash: str
    size_bytes: int
    created_at: datetime
    stored_at: datetime

    model_config = {"from_attributes": True}


class PlanOutcomeDB(BaseModel):
    """Database model for plan outcomes."""
    outcome_id: UUID
    plan_id: str
    success: bool
    error_type: Optional[str]
    error_details: Optional[Dict[str, Any]]
    execution_start: datetime
    execution_end: datetime
    total_steps: int
    failed_step: Optional[int]
    context_data: Optional[Dict[str, Any]]

    model_config = {"from_attributes": True}


class PlanEmbeddingDB(BaseModel):
    """Database model for plan embeddings."""
    embedding_id: UUID
    plan_id: str
    vector: List[float]
    model_version: str
    created_at: datetime
    vector_norm: float

    model_config = {"from_attributes": True}


class PlanMetricsDB(BaseModel):
    """Database model for plan metrics."""
    metrics_id: UUID
    plan_id: str
    preview_latency_ms: Optional[int]
    execute_latency_ms: int
    step_timings: Optional[List[Dict[str, Any]]]
    resource_usage: Optional[Dict[str, Any]]

    model_config = {"from_attributes": True}


# Request/Response models
class StorePlanRequest(BaseModel):
    """Request to store plan with outcome and metrics."""
    plan: Plan
    signature: Signature
    outcome: PlanOutcome
    metrics: PlanMetrics

    @model_validator(mode='after')
    def validate_plan_size(self):
        """Validate plan doesn't exceed size limits."""
        size_bytes = self.plan.get_size_bytes()
        if size_bytes > 1024 * 1024:  # 1MB limit
            raise ValueError(f"Plan size ({size_bytes} bytes) exceeds 1MB limit")
        return self


class StorePlanResponse(BaseModel):
    """Response from successful plan storage."""
    status: str = Field(default="ok")
    plan_id: str
    stored_at: datetime
    embedding_queued: bool

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "ok",
                "plan_id": "01HX123456789ABCDEFGHIJKLM",
                "stored_at": "2025-12-29T10:30:00Z",
                "embedding_queued": True
            }
        }
    }


class PlanQueryRequest(BaseModel):
    """Request to query plans by intent or similarity."""
    intent_type: Optional[str] = Field(None, min_length=1, max_length=64)
    query_text: Optional[str] = Field(None, min_length=1, max_length=1024)
    success_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    similarity_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    limit: int = Field(default=50, ge=1, le=1000)
    recency_days: Optional[int] = Field(None, ge=1)

    @model_validator(mode='after')
    def validate_query_parameters(self):
        """Ensure at least one query parameter is provided."""
        if not self.intent_type and not self.query_text:
            raise ValueError("Either intent_type or query_text must be provided")
        return self


class PlanPattern(BaseModel):
    """Plan pattern with success metrics for Evidence Item conversion."""
    plan_id: str
    intent_type: str
    success_rate: float
    avg_execution_time_ms: float
    steps_count: int
    pattern_summary: str
    total_executions: int
    last_execution: datetime
    confidence: float = Field(..., ge=0.0, le=1.0)

    def to_evidence_item(self) -> EvidenceItem:
        """
        Convert plan pattern to Evidence Item format.
        
        Returns:
            EvidenceItem with type="plan" for ContextRAG integration
        """
        return EvidenceItem(
            type="plan",
            key=f"{self.intent_type}_pattern_{hash(self.plan_id) % 1000}",
            value={
                "intent": self.intent_type,
                "success_rate": self.success_rate,
                "avg_execution_time_ms": self.avg_execution_time_ms,
                "steps_count": self.steps_count,
                "pattern_summary": self.pattern_summary,
                "total_executions": self.total_executions,
                "last_execution": self.last_execution.isoformat()
            },
            confidence=self.confidence,
            source_ref=f"planlibrary:plans/{self.plan_id}",
            ttl_days=None,  # Permanent storage
            tier=3  # Historical data tier
        )


class SimilarityMatch(BaseModel):
    """Similarity search result with scoring."""
    plan_id: str
    intent_type: str
    similarity_score: float = Field(..., ge=0.0, le=1.0)
    success_rate: float = Field(..., ge=0.0, le=1.0)
    relevance_score: float = Field(..., ge=0.0, le=1.0)
    plan_pattern: PlanPattern

    def to_evidence_item(self) -> EvidenceItem:
        """Convert similarity match to Evidence Item."""
        evidence = self.plan_pattern.to_evidence_item()
        # Update key to reflect similarity context
        evidence.key = f"similar_{self.intent_type}_{hash(self.plan_id) % 1000}"
        # Use relevance score as confidence
        evidence.confidence = self.relevance_score
        return evidence


class ErrorResponse(BaseModel):
    """Standardized error response."""
    status: str = Field(default="error")
    error_code: str
    message: str
    details: Optional[Dict[str, Any]] = Field(None)

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "error",
                "error_code": "INVALID_SIGNATURE",
                "message": "Plan signature verification failed",
                "details": {"plan_id": "01HX123456789ABCDEFGHIJKLM"}
            }
        }
    }