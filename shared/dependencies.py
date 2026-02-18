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
