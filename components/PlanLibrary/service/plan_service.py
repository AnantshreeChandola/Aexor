"""
Plan Service - Core business logic for plan storage and retrieval.

Handles plan storage with signature verification, canonicalization,
and query operations for intent-based and similarity search.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from uuid import UUID

from ..domain.models import (
    Plan, Signature, PlanOutcome, PlanMetrics, 
    StorePlanResponse, PlanPattern, PlanDB, ErrorResponse
)
from ..adapters.db import DatabaseAdapter
from ..adapters.signature_verifier import SignatureVerifier
from .vector_service import VectorService

logger = logging.getLogger(__name__)


class PlanLibraryError(Exception):
    """Base exception for PlanLibrary operations."""
    pass


class InvalidSignatureError(PlanLibraryError):
    """Raised when plan signature verification fails."""
    def __init__(self, plan_id: str):
        self.plan_id = plan_id
        super().__init__(f"Signature verification failed for plan {plan_id}")


class DuplicatePlanError(PlanLibraryError):
    """Raised when attempting to store duplicate plan ID."""
    def __init__(self, plan_id: str):
        self.plan_id = plan_id
        super().__init__(f"Plan {plan_id} already exists")


class PlanTooLargeError(PlanLibraryError):
    """Raised when plan exceeds size limits."""
    def __init__(self, size_bytes: int, max_bytes: int = 1024*1024):
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes
        super().__init__(f"Plan size ({size_bytes} bytes) exceeds {max_bytes} byte limit")


class PlanNotFoundError(PlanLibraryError):
    """Raised when requested plan is not found."""
    def __init__(self, plan_id: str):
        self.plan_id = plan_id
        super().__init__(f"Plan {plan_id} not found")


class PlanService:
    """
    Core plan storage and retrieval service.
    
    Implements business logic for:
    - Plan storage with signature verification
    - Plan canonicalization and hash generation
    - Intent-based plan queries with success filtering
    - Plan pattern extraction for optimization
    """
    
    def __init__(
        self,
        db_adapter: DatabaseAdapter,
        signature_verifier: SignatureVerifier,
        vector_service: VectorService
    ):
        """
        Initialize plan service with required adapters.
        
        Args:
            db_adapter: Database adapter for plan persistence
            signature_verifier: Ed25519 signature verification
            vector_service: Vector embedding and similarity search
        """
        self.db_adapter = db_adapter
        self.signature_verifier = signature_verifier
        self.vector_service = vector_service
        
        logger.info("PlanService initialized")

    async def store_plan(
        self,
        plan: Plan,
        signature: Signature,
        outcome: PlanOutcome,
        metrics: PlanMetrics
    ) -> StorePlanResponse:
        """
        Store executed plan with outcome and metrics.
        
        Performs:
        1. Signature verification using Ed25519
        2. Plan canonicalization and hash generation  
        3. Database transaction for atomic storage
        4. Background embedding generation queue
        
        Args:
            plan: Complete plan object
            signature: Ed25519 signature for plan verification
            outcome: Execution outcome (success/failure)
            metrics: Performance metrics
            
        Returns:
            StorePlanResponse with status and plan details
            
        Raises:
            InvalidSignatureError: If signature verification fails
            DuplicatePlanError: If plan_id already exists
            PlanTooLargeError: If plan exceeds size limits
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # Step 1: Validate plan size
            size_bytes = plan.get_size_bytes()
            if size_bytes > 1024 * 1024:  # 1MB limit
                raise PlanTooLargeError(size_bytes)
            
            # Step 2: Verify signature
            canonical_json = plan.to_canonical_json()
            is_valid = await self.signature_verifier.verify_signature(
                canonical_json, signature
            )
            
            if not is_valid:
                logger.warning(f"Signature verification failed for plan {plan.plan_id}")
                raise InvalidSignatureError(plan.plan_id)
            
            # Step 3: Check for duplicate plan_id
            existing_plan = await self.db_adapter.get_plan_by_id(plan.plan_id)
            if existing_plan:
                logger.warning(f"Attempt to store duplicate plan {plan.plan_id}")
                raise DuplicatePlanError(plan.plan_id)
            
            # Step 4: Store plan atomically
            plan_hash = plan.get_plan_hash()
            stored_at = datetime.now(timezone.utc)
            
            plan_db = PlanDB(
                plan_id=plan.plan_id,
                canonical_json=plan.model_dump(),
                signature_data=signature.model_dump(),
                intent_type=plan.intent.type,
                step_count=len(plan.graph),
                plan_hash=plan_hash,
                size_bytes=size_bytes,
                created_at=plan.meta.created_at,
                stored_at=stored_at
            )
            
            success = await self.db_adapter.store_plan_transaction(
                plan_db, outcome, metrics
            )
            
            if not success:
                raise PlanLibraryError(f"Failed to store plan {plan.plan_id}")
            
            # Step 5: Queue embedding generation (async, non-blocking)
            embedding_queued = False
            try:
                plan_text = self._extract_plan_text(plan)
                embedding_queued = await self.vector_service.queue_embedding_generation(
                    plan.plan_id, plan_text
                )
            except Exception as e:
                # Don't fail storage if embedding generation fails
                logger.warning(f"Failed to queue embedding for plan {plan.plan_id}: {e}")
            
            # Log successful storage
            latency_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            logger.info(
                "Plan stored successfully",
                extra={
                    "plan_id": plan.plan_id,
                    "intent_type": plan.intent.type,
                    "step_count": len(plan.graph),
                    "size_bytes": size_bytes,
                    "storage_latency_ms": latency_ms,
                    "embedding_queued": embedding_queued,
                    "component": "PlanLibrary"
                }
            )
            
            return StorePlanResponse(
                plan_id=plan.plan_id,
                stored_at=stored_at,
                embedding_queued=embedding_queued
            )
            
        except (InvalidSignatureError, DuplicatePlanError, PlanTooLargeError):
            # Re-raise domain errors
            raise
        except Exception as e:
            logger.error(f"Unexpected error storing plan {plan.plan_id}: {e}")
            raise PlanLibraryError(f"Storage failed: {str(e)}")

    async def get_plans_by_intent(
        self,
        intent_type: str,
        success_threshold: float = 0.7,
        limit: int = 50,
        recency_days: Optional[int] = None
    ) -> List[PlanPattern]:
        """
        Query plans by intent type with success rate filtering.
        
        Returns plans sorted by success rate descending, then by
        total executions descending for tie-breaking.
        
        Args:
            intent_type: Intent type to filter by
            success_threshold: Minimum success rate (0.0-1.0)
            limit: Maximum number of results (max 1000)
            recency_days: Filter plans from last N days (optional)
            
        Returns:
            List of PlanPattern objects sorted by relevance
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # Validate parameters
            if not intent_type or len(intent_type.strip()) == 0:
                raise ValueError("intent_type cannot be empty")
            
            if not (0.0 <= success_threshold <= 1.0):
                raise ValueError("success_threshold must be between 0.0 and 1.0")
            
            if limit <= 0 or limit > 1000:
                raise ValueError("limit must be between 1 and 1000")
            
            # Query database for matching plans
            plan_patterns = await self.db_adapter.get_plans_by_intent_with_success(
                intent_type=intent_type,
                success_threshold=success_threshold,
                limit=limit,
                recency_days=recency_days
            )
            
            # Log query performance
            latency_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            logger.info(
                "Intent-based query completed",
                extra={
                    "intent_type": intent_type,
                    "success_threshold": success_threshold,
                    "results_count": len(plan_patterns),
                    "query_latency_ms": latency_ms,
                    "component": "PlanLibrary"
                }
            )
            
            return plan_patterns
            
        except ValueError:
            # Re-raise validation errors
            raise
        except Exception as e:
            logger.error(f"Error querying plans by intent {intent_type}: {e}")
            raise PlanLibraryError(f"Intent query failed: {str(e)}")

    async def get_plan_by_id(self, plan_id: str) -> Optional[Plan]:
        """
        Retrieve specific plan by ID.
        
        Args:
            plan_id: ULID identifier for the plan
            
        Returns:
            Plan object if found, None if not found
            
        Raises:
            ValueError: If plan_id format is invalid
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # Validate plan_id format (basic ULID check)
            if not plan_id or len(plan_id) != 26:
                raise ValueError("plan_id must be a 26-character ULID")
            
            # Query database
            plan_db = await self.db_adapter.get_plan_by_id(plan_id)
            
            if not plan_db:
                return None
            
            # Convert to domain model
            plan = Plan.model_validate(plan_db.canonical_json)
            
            # Log retrieval
            latency_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            logger.debug(
                "Plan retrieved successfully",
                extra={
                    "plan_id": plan_id,
                    "retrieval_latency_ms": latency_ms,
                    "component": "PlanLibrary"
                }
            )
            
            return plan
            
        except ValueError:
            # Re-raise validation errors
            raise
        except Exception as e:
            logger.error(f"Error retrieving plan {plan_id}: {e}")
            raise PlanLibraryError(f"Plan retrieval failed: {str(e)}")

    def _extract_plan_text(self, plan: Plan) -> str:
        """
        Extract text representation of plan for embedding generation.
        
        Combines intent description, operation names, and key parameters
        into a text suitable for semantic similarity search.
        
        Args:
            plan: Plan object to extract text from
            
        Returns:
            Text representation for embedding generation
        """
        parts = []
        
        # Include intent information
        parts.append(f"Intent: {plan.intent.type}")
        if plan.intent.description:
            parts.append(f"Description: {plan.intent.description}")
        
        # Include key parameters
        if plan.intent.parameters:
            key_params = [f"{k}={v}" for k, v in plan.intent.parameters.items()]
            parts.append(f"Parameters: {', '.join(key_params)}")
        
        # Include operations sequence
        operations = [step.operation for step in plan.graph]
        parts.append(f"Operations: {' → '.join(operations)}")
        
        # Include step count and constraints
        parts.append(f"Steps: {len(plan.graph)}")
        if plan.constraints:
            constraints = [f"{k}={v}" for k, v in plan.constraints.items()]
            parts.append(f"Constraints: {', '.join(constraints)}")
        
        return ". ".join(parts)

    async def get_success_rate_analytics(
        self, 
        intent_type: Optional[str] = None,
        timeframe_days: int = 30
    ) -> Dict[str, float]:
        """
        Calculate success rates by intent type.
        
        Args:
            intent_type: Specific intent to analyze (None for all)
            timeframe_days: Analysis timeframe in days
            
        Returns:
            Dictionary mapping intent types to success rates
        """
        try:
            return await self.db_adapter.calculate_success_rates(
                intent_type=intent_type,
                timeframe_days=timeframe_days
            )
        except Exception as e:
            logger.error(f"Error calculating success rates: {e}")
            raise PlanLibraryError(f"Analytics failed: {str(e)}")

    async def health_check(self) -> Dict[str, Any]:
        """
        Check health of all plan service dependencies.
        
        Returns:
            Health status dictionary
        """
        health = {
            "service": "PlanService",
            "status": "healthy",
            "dependencies": {}
        }
        
        try:
            # Check database connectivity
            db_healthy = await self.db_adapter.health_check()
            health["dependencies"]["database"] = "healthy" if db_healthy else "unhealthy"
            
            # Check vector service
            vector_healthy = await self.vector_service.health_check()
            health["dependencies"]["vector_service"] = "healthy" if vector_healthy else "unhealthy"
            
            # Overall status
            if not db_healthy:
                health["status"] = "unhealthy"
                health["error"] = "Database connectivity failed"
                
        except Exception as e:
            health["status"] = "unhealthy"
            health["error"] = str(e)
            
        return health