"""
Shared FastAPI Dependencies

Thin Depends() functions that retrieve service singletons from app.state.
Services are initialized in the lifespan function (shared/app.py).

Usage:
    from shared.dependencies import get_plan_service

    @router.post("/plans")
    async def store_plan(service: PlanService = Depends(get_plan_service)):
        ...
"""

from typing import Any

from fastapi import Request


def get_plan_service(request: Request) -> Any:
    """Get PlanService singleton from app state."""
    return request.app.state.plan_service


def get_analytics_service(request: Request) -> Any:
    """Get AnalyticsService singleton from app state."""
    return request.app.state.analytics_service


def get_preference_service(request: Request) -> Any:
    """Get PreferenceService singleton from app state."""
    return request.app.state.preference_service


def get_fact_service(request: Request) -> Any:
    """Get FactService singleton from app state."""
    return request.app.state.fact_service


def get_pattern_service(request: Request) -> Any:
    """Get PatternService singleton from app state."""
    return request.app.state.pattern_service


def get_registry_service(request: Request) -> Any:
    """Get RegistryService singleton from app state."""
    return request.app.state.registry_service


def get_signer_service(request: Request) -> Any:
    """Get SignerService singleton from app state."""
    return request.app.state.signer_service


def get_vector_index_service(request: Request) -> Any:
    """Get VectorIndexService singleton from app state."""
    return request.app.state.vector_index_service


def get_plan_writer_service(request: Request) -> Any:
    """Get PlanWriterService singleton from app state."""
    return request.app.state.plan_writer_service


def get_context_rag_service(request: Request) -> Any:
    """Get ContextRAGService singleton from app state."""
    return request.app.state.context_rag_service
