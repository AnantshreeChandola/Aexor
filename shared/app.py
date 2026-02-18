"""
Application Factory — FastAPI App with Lifespan DI

Creates the FastAPI application, initializes all services at startup
via the lifespan context manager, registers middleware and routers.

Usage:
    from shared.app import create_app
    app = create_app()
"""

import logging
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

    logger.info("All services initialized")

    yield

    # --- Shutdown ---
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
    from components.PlanLibrary.api.routes import router as plan_router
    from components.ProfileStore.api.routes import router as profile_router
    from shared.api.auth_routes import router as auth_router

    app.include_router(auth_router)
    app.include_router(plan_router)
    app.include_router(profile_router)

    # Root health check
    @app.get("/health")
    async def root_health():
        return {"status": "ok", "service": "personal-agent"}

    return app
