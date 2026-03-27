"""
Application Factory — FastAPI App with Lifespan DI

Creates the FastAPI application, initializes all services at startup
via the lifespan context manager, registers middleware and routers.

Usage:
    from shared.app import create_app
    app = create_app()
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan — startup and shutdown.

    Startup: constructs all services and stores them on app.state.
    Shutdown: closes database connections.
    """
    # Lazy imports to avoid circular dependencies at module level
    from components.PlanLibrary.adapters.db import DatabaseAdapter as PlanDBAdapter
    from components.PlanLibrary.adapters.signature_verifier import SignatureVerifier
    from components.PlanLibrary.service.analytics_service import AnalyticsService
    from components.PlanLibrary.service.plan_service import PlanService
    from components.ProfileStore.adapters.db import DatabaseAdapter as ProfileDBAdapter
    from components.ProfileStore.adapters.encryption import get_encryption_adapter
    from components.ProfileStore.adapters.schema_registry import get_schema_registry
    from components.ProfileStore.service.preference_service import PreferenceService
    from shared.database.adapter import SharedDatabaseAdapter

    # --- Startup ---

    # Shared infrastructure
    db = SharedDatabaseAdapter()
    app.state.db = db

    # PlanLibrary services
    plan_db = PlanDBAdapter()
    app.state.plan_service = PlanService(
        db_adapter=plan_db,
        signature_verifier=SignatureVerifier(),
    )
    app.state.analytics_service = AnalyticsService(db_adapter=plan_db)

    # ProfileStore services
    app.state.preference_service = PreferenceService(
        db_adapter=ProfileDBAdapter(),
        schema_registry=get_schema_registry(),
        encryption_adapter=get_encryption_adapter(),
    )

    # PluginRegistry services
    from components.PluginRegistry.adapters.db import RegistryDatabaseAdapter
    from components.PluginRegistry.service.registry_service import RegistryService

    registry_db = RegistryDatabaseAdapter()
    app.state.registry_service = RegistryService(db_adapter=registry_db)

    # Signer service (library — no DB, no routes)
    from components.Signer.service.signer_service import create_signer_service

    app.state.signer_service = create_signer_service()

    # History services
    from components.History.adapters.db import DatabaseAdapter as HistoryDBAdapter
    from components.History.service.evidence_service import EvidenceService
    from components.History.service.fact_service import FactService
    from components.History.service.pattern_service import PatternService

    history_db = HistoryDBAdapter()
    evidence_service = EvidenceService()
    pattern_service = PatternService(db_adapter=history_db)
    app.state.fact_service = FactService(
        db_adapter=history_db,
        evidence_service=evidence_service,
        pattern_service=pattern_service,
    )
    app.state.pattern_service = pattern_service
    app.state.history_db_adapter = history_db

    # VectorIndex service (library -- no routes, graceful degradation)
    try:
        from components.VectorIndex.domain.models import (
            EmbeddingModelError,
            VectorIndexUnavailableError,
        )
        from components.VectorIndex.service.vector_index_service import (
            create_vector_index_service,
        )

        app.state.vector_index_service = create_vector_index_service(db)
    except (VectorIndexUnavailableError, EmbeddingModelError) as exc:
        logger.warning("VectorIndex unavailable, degrading gracefully: %s", exc)
        app.state.vector_index_service = None
    except Exception as exc:
        logger.warning(
            "VectorIndex init failed unexpectedly, degrading gracefully: %s",
            exc,
        )
        app.state.vector_index_service = None

    # PlanWriter service (library -- no routes)
    from components.PlanWriter.service.plan_writer_service import (
        create_plan_writer_service,
    )

    app.state.plan_writer_service = create_plan_writer_service(
        plan_service=app.state.plan_service,
        fact_service=app.state.fact_service,
        vector_index_service=app.state.vector_index_service,
    )

    # ContextRAG service (library -- no routes)
    from components.ContextRAG.service.context_rag_service import (
        create_context_rag_service,
    )

    app.state.context_rag_service = create_context_rag_service(
        preference_service=app.state.preference_service,
        fact_service=app.state.fact_service,
        pattern_service=app.state.pattern_service,
        plan_service=app.state.plan_service,
        vector_index_service=app.state.vector_index_service,
    )

    # Planner service (library -- no routes)
    from components.Planner.service.planner_service import create_planner_service

    try:
        app.state.planner_service = create_planner_service(
            context_rag_service=app.state.context_rag_service,
            registry_service=app.state.registry_service,
            signer_service=app.state.signer_service,
            plan_service=app.state.plan_service,
        )
    except Exception as exc:
        logger.warning("Planner init failed (ANTHROPIC_API_KEY may not be set): %s", exc)
        app.state.planner_service = None

    # Intake service (API layer -- Redis sessions, LLM parsing)
    intake_redis = None
    try:
        import redis.asyncio as aioredis

        from components.Intake.service.intake_service import create_intake_service
        from components.Planner.adapters.llm_adapter import AnthropicAdapter

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        intake_redis = aioredis.from_url(redis_url, decode_responses=True)

        # Reuse the shared LLM adapter from Planner if available, else create one
        llm_adapter: AnthropicAdapter | None = None
        if app.state.planner_service is not None:
            llm_adapter = app.state.planner_service._llm  # noqa: SLF001
        if llm_adapter is None:
            llm_adapter = AnthropicAdapter()

        app.state.intake_service = create_intake_service(
            redis_client=intake_redis,
            llm_adapter=llm_adapter,
            planner_service=app.state.planner_service,
            preference_service=app.state.preference_service,
        )
    except Exception as exc:
        logger.warning("Intake init failed: %s", exc)
        app.state.intake_service = None

    logger.info("All services initialized")

    yield

    # --- Shutdown ---
    if intake_redis is not None:
        await intake_redis.close()
    await db.close()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Personal Agent",
        description="Preview-first personal assistant with deterministic planning",
        lifespan=lifespan,
    )

    # Middleware (order matters — last added = first executed)
    from shared.api.error_handlers import create_error_handler_middleware
    from shared.middleware.auth import AuthMiddleware

    app.middleware("http")(create_error_handler_middleware())
    app.add_middleware(AuthMiddleware)

    # Routers
    from components.History.api.routes import router as history_router
    from components.Intake.api.routes import router as intake_router
    from components.PlanLibrary.api.routes import router as plan_router
    from components.PluginRegistry.api.routes import router as registry_router
    from components.ProfileStore.api.routes import router as profile_router
    from shared.api.auth_routes import router as auth_router

    app.include_router(auth_router)
    app.include_router(plan_router)
    app.include_router(profile_router)
    app.include_router(history_router)
    app.include_router(registry_router)
    app.include_router(intake_router)

    # Root health check
    @app.get("/health")
    async def root_health():
        return {"status": "ok", "service": "personal-agent"}

    return app
