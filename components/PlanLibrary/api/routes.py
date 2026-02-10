"""
PlanLibrary API Routes

FastAPI endpoints for plan storage, querying, and health monitoring.
Thin wrappers that delegate to service layer with proper error handling.
"""

import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.responses import JSONResponse

from shared.api.error_handlers import ErrorHandlerMixin
from shared.schemas.evidence import EvidenceItem

from ..domain.models import (
    StorePlanRequest, StorePlanResponse, PlanQueryRequest,
    ErrorResponse, Plan, PlanPattern, SimilarityMatch
)
from ..service.plan_service import (
    PlanService, PlanLibraryError, InvalidSignatureError, 
    DuplicatePlanError, PlanTooLargeError, PlanNotFoundError
)
from ..service.vector_service import VectorService, VectorSearchUnavailableError
from ..service.analytics_service import AnalyticsService
from .dependencies import get_plan_service, get_vector_service, get_analytics_service

logger = logging.getLogger(__name__)

# Create router for PlanLibrary endpoints
router = APIRouter(prefix="/plans", tags=["plans"])

# Error handler for consistent error responses
error_handler = ErrorHandlerMixin()


@router.post("/", response_model=StorePlanResponse)
async def store_plan_endpoint(
    request: StorePlanRequest,
    plan_service: PlanService = Depends(get_plan_service)
) -> StorePlanResponse:
    """
    Store executed plan with outcome and metrics.
    
    Stores a plan with its signature, execution outcome, and performance
    metrics for future learning and optimization.
    
    Args:
        request: StorePlanRequest with plan, signature, outcome, and metrics
        
    Returns:
        StorePlanResponse with storage confirmation
        
    Raises:
        400: Invalid plan data or signature
        409: Plan ID already exists
        413: Plan exceeds size limits
        500: Internal storage error
    """
    try:
        result = await plan_service.store_plan(
            plan=request.plan,
            signature=request.signature,
            outcome=request.outcome,
            metrics=request.metrics
        )
        
        return result
        
    except InvalidSignatureError as e:
        logger.warning(f"Invalid signature for plan {e.plan_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "INVALID_SIGNATURE",
                "message": "Plan signature verification failed",
                "details": {"plan_id": e.plan_id}
            }
        )
        
    except DuplicatePlanError as e:
        logger.warning(f"Duplicate plan ID {e.plan_id}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "DUPLICATE_PLAN_ID",
                "message": f"Plan {e.plan_id} already exists",
                "details": {"plan_id": e.plan_id}
            }
        )
        
    except PlanTooLargeError as e:
        logger.warning(f"Plan exceeds size limit: {e.size_bytes} bytes")
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error_code": "PLAN_TOO_LARGE",
                "message": f"Plan size ({e.size_bytes} bytes) exceeds limit",
                "details": {
                    "size_bytes": e.size_bytes,
                    "max_bytes": e.max_bytes
                }
            }
        )
        
    except PlanLibraryError as e:
        logger.error(f"Plan storage error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "STORAGE_ERROR",
                "message": "Failed to store plan",
                "details": {"error": str(e)}
            }
        )
        
    except Exception as e:
        logger.error(f"Unexpected error storing plan: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": "Internal server error",
                "details": {}
            }
        )


@router.get("/by-intent", response_model=List[EvidenceItem])
async def get_plans_by_intent_endpoint(
    intent_type: str,
    success_threshold: float = 0.7,
    limit: int = 50,
    recency_days: Optional[int] = None,
    plan_service: PlanService = Depends(get_plan_service)
) -> List[EvidenceItem]:
    """
    Query plans by intent type with success filtering.
    
    Returns successful plan patterns for the specified intent type,
    formatted as Evidence Items for ContextRAG integration.
    
    Args:
        intent_type: Intent type to filter by
        success_threshold: Minimum success rate (0.0-1.0)
        limit: Maximum number of results (1-1000)
        recency_days: Filter plans from last N days (optional)
        
    Returns:
        List of Evidence Items with type="plan"
        
    Raises:
        400: Invalid query parameters
        500: Query execution error
    """
    try:
        # Validate parameters
        if not intent_type or intent_type.strip() == "":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "INVALID_QUERY",
                    "message": "intent_type cannot be empty",
                    "details": {}
                }
            )
        
        if not (0.0 <= success_threshold <= 1.0):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "INVALID_QUERY",
                    "message": "success_threshold must be between 0.0 and 1.0",
                    "details": {"success_threshold": success_threshold}
                }
            )
        
        if limit <= 0 or limit > 1000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "INVALID_QUERY",
                    "message": "limit must be between 1 and 1000",
                    "details": {"limit": limit}
                }
            )
        
        # Execute query
        plan_patterns = await plan_service.get_plans_by_intent(
            intent_type=intent_type,
            success_threshold=success_threshold,
            limit=limit,
            recency_days=recency_days
        )
        
        # Convert to Evidence Items
        evidence_items = [pattern.to_evidence_item() for pattern in plan_patterns]
        
        logger.info(
            f"Intent query completed: {len(evidence_items)} results for {intent_type}"
        )
        
        return evidence_items
        
    except HTTPException:
        # Re-raise validation errors
        raise
        
    except PlanLibraryError as e:
        logger.error(f"Plan query error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "QUERY_ERROR",
                "message": "Failed to execute plan query",
                "details": {"error": str(e)}
            }
        )
        
    except Exception as e:
        logger.error(f"Unexpected error in intent query: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": "Internal server error",
                "details": {}
            }
        )


@router.get("/similarity", response_model=List[EvidenceItem])
async def similarity_search_endpoint(
    query_text: str,
    similarity_threshold: float = 0.5,
    success_threshold: float = 0.5,
    limit: int = 10,
    vector_service: VectorService = Depends(get_vector_service)
) -> List[EvidenceItem]:
    """
    Find similar plans using vector embeddings.
    
    Uses semantic similarity search to find plans similar to the query text,
    filtered by similarity and success rate thresholds.
    
    Args:
        query_text: Text to find similar plans for
        similarity_threshold: Minimum similarity score (0.0-1.0)
        success_threshold: Minimum success rate filter (0.0-1.0)
        limit: Maximum number of results (1-100)
        
    Returns:
        List of Evidence Items with similar plans
        
    Raises:
        400: Invalid query parameters
        503: Vector search unavailable
        500: Search execution error
    """
    try:
        # Validate parameters
        if not query_text or query_text.strip() == "":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "INVALID_QUERY",
                    "message": "query_text cannot be empty",
                    "details": {}
                }
            )
        
        if not (0.0 <= similarity_threshold <= 1.0):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "INVALID_QUERY",
                    "message": "similarity_threshold must be between 0.0 and 1.0",
                    "details": {"similarity_threshold": similarity_threshold}
                }
            )
        
        if limit <= 0 or limit > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "INVALID_QUERY",
                    "message": "limit must be between 1 and 100",
                    "details": {"limit": limit}
                }
            )
        
        # Execute similarity search
        similarity_matches = await vector_service.similarity_search(
            query_text=query_text,
            similarity_threshold=similarity_threshold,
            limit=limit,
            success_threshold=success_threshold
        )
        
        # Convert to Evidence Items
        evidence_items = [match.to_evidence_item() for match in similarity_matches]
        
        logger.info(
            f"Similarity search completed: {len(evidence_items)} results for query length {len(query_text)}"
        )
        
        return evidence_items
        
    except HTTPException:
        # Re-raise validation errors
        raise
        
    except VectorSearchUnavailableError as e:
        logger.warning(f"Vector search unavailable: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "VECTOR_SEARCH_UNAVAILABLE",
                "message": "Vector similarity search is temporarily unavailable",
                "details": {"reason": str(e)}
            }
        )
        
    except Exception as e:
        logger.error(f"Unexpected error in similarity search: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "SEARCH_ERROR",
                "message": "Failed to execute similarity search",
                "details": {}
            }
        )


@router.get("/{plan_id}", response_model=Dict[str, Any])
async def get_plan_by_id_endpoint(
    plan_id: str,
    plan_service: PlanService = Depends(get_plan_service)
) -> Dict[str, Any]:
    """
    Retrieve specific plan by ID.
    
    Returns the complete plan object for the specified plan ID.
    
    Args:
        plan_id: ULID identifier for the plan
        
    Returns:
        Plan object as dictionary
        
    Raises:
        400: Invalid plan ID format
        404: Plan not found
        500: Retrieval error
    """
    try:
        # Validate plan ID format
        if not plan_id or len(plan_id) != 26:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "INVALID_PLAN_ID",
                    "message": "plan_id must be a 26-character ULID",
                    "details": {"plan_id": plan_id}
                }
            )
        
        # Retrieve plan
        plan = await plan_service.get_plan_by_id(plan_id)
        
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error_code": "PLAN_NOT_FOUND",
                    "message": f"Plan {plan_id} not found",
                    "details": {"plan_id": plan_id}
                }
            )
        
        return plan.model_dump()
        
    except HTTPException:
        # Re-raise HTTP errors
        raise
        
    except Exception as e:
        logger.error(f"Unexpected error retrieving plan {plan_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "RETRIEVAL_ERROR",
                "message": "Failed to retrieve plan",
                "details": {"plan_id": plan_id}
            }
        )


@router.get("/analytics/success-rates", response_model=Dict[str, Any])
async def get_success_rates_endpoint(
    timeframe_days: int = 30,
    analytics_service: AnalyticsService = Depends(get_analytics_service)
) -> Dict[str, Any]:
    """
    Get success rate analytics by intent type.
    
    Analyzes plan execution outcomes over the specified timeframe
    and returns success rates with confidence levels.
    
    Args:
        timeframe_days: Analysis timeframe in days (1-365)
        
    Returns:
        Dictionary with success rate analytics
        
    Raises:
        400: Invalid timeframe parameter
        500: Analytics calculation error
    """
    try:
        # Validate parameters
        if timeframe_days <= 0 or timeframe_days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "INVALID_QUERY",
                    "message": "timeframe_days must be between 1 and 365",
                    "details": {"timeframe_days": timeframe_days}
                }
            )
        
        # Calculate success rates
        success_analytics = await analytics_service.calculate_success_rates(
            timeframe_days=timeframe_days
        )
        
        # Format response
        response = {
            "timeframe_days": timeframe_days,
            "analytics": {
                intent_type: {
                    "success_rate": analytics.success_rate,
                    "total_executions": analytics.total_executions,
                    "successful_executions": analytics.successful_executions,
                    "avg_execution_time_ms": analytics.avg_execution_time_ms,
                    "confidence_level": analytics.confidence_level
                }
                for intent_type, analytics in success_analytics.items()
            },
            "generated_at": "2025-01-03T12:00:00Z"
        }
        
        logger.info(f"Success rate analytics generated for {len(success_analytics)} intent types")
        
        return response
        
    except HTTPException:
        # Re-raise validation errors
        raise
        
    except Exception as e:
        logger.error(f"Error generating success rate analytics: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "ANALYTICS_ERROR",
                "message": "Failed to calculate success rates",
                "details": {}
            }
        )


@router.get("/health", response_model=Dict[str, Any])
async def health_check_endpoint(
    plan_service: PlanService = Depends(get_plan_service),
    vector_service: VectorService = Depends(get_vector_service),
    analytics_service: AnalyticsService = Depends(get_analytics_service)
) -> Dict[str, Any]:
    """
    Check health of PlanLibrary component and dependencies.
    
    Returns health status for all major subsystems including
    database, vector search, and embedding services.
    
    Returns:
        Health status dictionary
    """
    try:
        # Check all service health in parallel
        plan_health = await plan_service.health_check()
        vector_health = await vector_service.health_check()
        analytics_health = await analytics_service.health_check()
        
        # Determine overall health
        overall_healthy = all([
            plan_health.get("status") == "healthy",
            vector_health,
            analytics_health
        ])
        
        health_response = {
            "service": "PlanLibrary",
            "status": "healthy" if overall_healthy else "unhealthy",
            "timestamp": "2025-01-03T12:00:00Z",
            "dependencies": {
                "plan_service": plan_health,
                "vector_service": {
                    "status": "healthy" if vector_health else "unhealthy",
                    "embedding_queue": await vector_service.get_queue_status()
                },
                "analytics_service": {
                    "status": "healthy" if analytics_health else "unhealthy"
                }
            }
        }
        
        return health_response
        
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return {
            "service": "PlanLibrary",
            "status": "unhealthy",
            "timestamp": "2025-01-03T12:00:00Z",
            "error": str(e)
        }