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


def get_vector_index_service(request: Request) -> Any:
    """Get VectorIndexService singleton from app state."""
    return request.app.state.vector_index_service


def get_plan_writer_service(request: Request) -> Any:
    """Get PlanWriterService singleton from app state."""
    return request.app.state.plan_writer_service


def get_context_rag_service(request: Request) -> Any:
    """Get ContextRAGService singleton from app state."""
    return request.app.state.context_rag_service


def get_planner_service(request: Request) -> Any:
    """Get PlannerService singleton from app state."""
    return request.app.state.planner_service


def get_intake_service(request: Request) -> Any:
    """Get IntakeService singleton from app state."""
    return request.app.state.intake_service


def get_policy_service(request: Request) -> Any:
    """Get PolicyService singleton from app state."""
    return request.app.state.policy_service


def get_execute_service(request: Request) -> Any:
    """Get ExecuteService singleton from app state."""
    return request.app.state.execute_service


def get_preview_service(request: Request) -> Any:
    """Get PreviewService singleton from app state."""
    return request.app.state.preview_service


def get_approval_service(request: Request) -> Any:
    """Get ApprovalService singleton from app state."""
    return request.app.state.approval_service


def get_tracker_service(request: Request) -> Any:
    """Get TrackerService singleton from app state."""
    return request.app.state.tracker_service


def get_monitor_service(request: Request) -> Any:
    """Get MonitorService singleton from app state."""
    return request.app.state.monitor_service


def get_audit_service(request: Request) -> Any:
    """Get AuditService singleton from app state."""
    return request.app.state.audit_service


def get_filter_service(request: Request) -> Any:
    """Get FilterService singleton from app state."""
    return request.app.state.filter_service


def get_scheduler_service(request: Request) -> Any:
    """Get SchedulerService singleton from app state (may be None)."""
    return getattr(request.app.state, "scheduler_service", None)


def get_llm_adapter(request: Request) -> Any:
    """Get LLMAdapter singleton from app state (may be None)."""
    return getattr(request.app.state, "llm_adapter", None)
