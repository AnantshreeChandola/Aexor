"""
FastAPI Dependencies for PlanLibrary

Dependency injection for service layer components.
Provides singleton instances of services and adapters.
"""

import logging
from functools import lru_cache

from ..service.plan_service import PlanService
from ..service.vector_service import VectorService
from ..service.analytics_service import AnalyticsService
from ..adapters.db import DatabaseAdapter
from ..adapters.signature_verifier import SignatureVerifier
from ..adapters.vector_db import VectorAdapter
from ..adapters.embedding_client import EmbeddingClient

logger = logging.getLogger(__name__)


@lru_cache()
def get_database_adapter() -> DatabaseAdapter:
    """Get singleton DatabaseAdapter instance."""
    return DatabaseAdapter()


@lru_cache()
def get_signature_verifier() -> SignatureVerifier:
    """Get singleton SignatureVerifier instance."""
    return SignatureVerifier()


@lru_cache()
def get_vector_adapter() -> VectorAdapter:
    """Get singleton VectorAdapter instance."""
    return VectorAdapter()


@lru_cache()
def get_embedding_client() -> EmbeddingClient:
    """Get singleton EmbeddingClient instance."""
    return EmbeddingClient()


@lru_cache()
def get_vector_service() -> VectorService:
    """Get singleton VectorService instance."""
    embedding_client = get_embedding_client()
    vector_adapter = get_vector_adapter()
    return VectorService(
        embedding_client=embedding_client,
        vector_adapter=vector_adapter
    )


@lru_cache()
def get_analytics_service() -> AnalyticsService:
    """Get singleton AnalyticsService instance."""
    db_adapter = get_database_adapter()
    return AnalyticsService(db_adapter=db_adapter)


@lru_cache()
def get_plan_service() -> PlanService:
    """Get singleton PlanService instance."""
    db_adapter = get_database_adapter()
    signature_verifier = get_signature_verifier()
    vector_service = get_vector_service()
    
    return PlanService(
        db_adapter=db_adapter,
        signature_verifier=signature_verifier,
        vector_service=vector_service
    )


# Health check dependencies
async def get_component_health() -> dict:
    """Get health status of all components."""
    try:
        plan_service = get_plan_service()
        vector_service = get_vector_service()
        analytics_service = get_analytics_service()
        
        plan_health = await plan_service.health_check()
        vector_health = await vector_service.health_check()
        analytics_health = await analytics_service.health_check()
        
        return {
            "plan_service": plan_health,
            "vector_service": vector_health,
            "analytics_service": analytics_health
        }
    except Exception as e:
        logger.error(f"Component health check failed: {e}")
        return {"error": str(e)}