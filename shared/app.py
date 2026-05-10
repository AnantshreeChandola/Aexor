"""
Application Factory — FastAPI App with Lifespan DI

Creates the FastAPI application, initializes all services at startup
via the lifespan context manager, registers middleware and routers.

Usage:
    from shared.app import create_app
    app = create_app()
"""

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Configure root logger so all app logs (INFO+) are visible.
# Without this, Python defaults to WARNING and all INFO logs are silently dropped.
_log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
_log_format = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_log_datefmt = "%H:%M:%S"
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format=_log_format,
    datefmt=_log_datefmt,
)
# Also log to file so logs are accessible outside the server terminal.
_log_file = str(Path(__file__).parent.parent / "app.log")
_fh = logging.FileHandler(_log_file)
_fh.setLevel(getattr(logging, _log_level, logging.INFO))
_fh.setFormatter(logging.Formatter(_log_format, datefmt=_log_datefmt))
logging.getLogger().addHandler(_fh)

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
    )
    app.state.analytics_service = AnalyticsService(db_adapter=plan_db)

    # ProfileStore services
    app.state.preference_service = PreferenceService(
        db_adapter=ProfileDBAdapter(),
        schema_registry=get_schema_registry(),
        encryption_adapter=get_encryption_adapter(),
    )

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

    # MCP infrastructure (shared across Planner, ExecuteOrchestrator, PreviewOrchestrator)
    import httpx

    from shared.mcp.catalog import ToolCatalog
    from shared.mcp.config import load_composio_config_from_env, load_mcp_config_from_env
    from shared.mcp.session import MCPSessionManager
    from shared.mcp.url_manager import MCPUrlManager
    from shared.mcp.user_tool_cache import UserToolCache

    mcp_config = load_mcp_config_from_env()
    composio_config = load_composio_config_from_env()

    # Composio per-user URL manager (only when COMPOSIO_API_KEY is set)
    url_manager: MCPUrlManager | None = None
    if composio_config is not None:
        url_manager = MCPUrlManager(composio_config)

    mcp_http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30, connect=10),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    mcp_session_manager = MCPSessionManager(mcp_http_client)

    # Tool catalog — replaces Plugin Registry for tool definitions
    app.state.tool_catalog = ToolCatalog(
        config=mcp_config,
        http_client=mcp_http_client,
        session_manager=mcp_session_manager,
        composio_config=composio_config,
        url_manager=url_manager,
    )
    try:
        await app.state.tool_catalog.refresh()
    except Exception as exc:
        logger.warning("Tool catalog initial refresh failed: %s", exc)

    # Tool Discovery service (library -- no routes, graceful degradation)
    tool_discovery = None
    tool_discovery_enabled = os.environ.get(
        "TOOL_DISCOVERY_ENABLED", "true"
    ).lower() in ("true", "1", "yes")

    if tool_discovery_enabled and app.state.vector_index_service is not None:
        try:
            from components.Planner.adapters.cross_encoder_reranker import (
                CrossEncoderReranker,
            )
            from components.Planner.adapters.tool_discovery import (
                ToolDiscoveryService,
            )
            from components.Planner.adapters.tool_embedding_adapter import (
                ToolEmbeddingAdapter,
            )

            # Reuse the bi-encoder from VectorIndex
            embedding_adapter = app.state.vector_index_service._embedding

            tool_embedding_adapter = ToolEmbeddingAdapter(
                embedding_adapter=embedding_adapter,
                db_adapter=db,
            )

            # Cross-encoder reranker (optional — graceful if missing)
            reranker = None
            cross_encoder_path = os.environ.get(
                "CROSS_ENCODER_MODEL_PATH",
                str(Path("~/.cache/vectorindex/cross_encoder.onnx").expanduser()),
            )
            try:
                reranker = CrossEncoderReranker(model_path=cross_encoder_path)
            except Exception as exc:
                logger.warning("Cross-encoder model unavailable, Tier 2 reranking disabled: %s", exc)

            tool_discovery = ToolDiscoveryService(
                tool_embedding_adapter=tool_embedding_adapter,
                reranker=reranker,
                vector_index_service=app.state.vector_index_service,
                plan_service=app.state.plan_service,
                max_candidates=int(os.environ.get("TOOL_DISCOVERY_MAX_CANDIDATES", "20")),
                max_reranked=int(os.environ.get("TOOL_DISCOVERY_MAX_RERANKED", "5")),
                min_tools_threshold=int(os.environ.get("TOOL_DISCOVERY_MIN_THRESHOLD", "3")),
                plan_search_k=int(os.environ.get("TOOL_DISCOVERY_PLAN_SEARCH_K", "10")),
                tool_search_k=int(os.environ.get("TOOL_DISCOVERY_TOOL_SEARCH_K", "10")),
            )

            # Set catalog refresh callback to sync tool embeddings
            async def _on_tool_refresh(tools):
                try:
                    await tool_embedding_adapter.sync_tool_embeddings(tools)
                except Exception:
                    logger.warning("tool_embedding_sync_failed", exc_info=True)

            app.state.tool_catalog.set_refresh_callback(_on_tool_refresh)

            logger.info(
                "Tool discovery initialized",
                extra={"reranker_available": reranker is not None},
            )
        except Exception as exc:
            logger.warning("Tool discovery init failed, degrading gracefully: %s", exc)
            tool_discovery = None

    # Planner service (library -- no routes)
    from components.Planner.service.planner_service import create_planner_service

    try:
        app.state.planner_service = create_planner_service(
            context_rag_service=app.state.context_rag_service,
            tool_catalog=app.state.tool_catalog,
            plan_service=app.state.plan_service,
            tool_discovery=tool_discovery,
        )
    except Exception as exc:
        logger.warning("Planner init failed (ANTHROPIC_API_KEY may not be set): %s", exc)
        app.state.planner_service = None

    # PolicyEngine service (library -- no routes, cache-optional)
    from components.PolicyEngine.adapters.db import PolicyDatabaseAdapter
    from components.PolicyEngine.service.policy_service import create_policy_service

    policy_db = PolicyDatabaseAdapter()
    app.state.policy_service = create_policy_service(
        db_adapter=policy_db,
        redis_client=None,  # Redis wired below when available
    )

    # Intake service (API layer -- Redis sessions, LLM parsing)
    intake_redis = None
    try:
        import redis.asyncio as aioredis

        from components.Intake.service.intake_service import create_intake_service
        from components.Planner.adapters.llm_adapter import (
            LLMAdapter,
            LLMAdapterFactory,
            LLMConfig,
        )

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        intake_redis = aioredis.from_url(redis_url, decode_responses=True)

        # Reuse the shared LLM adapter from Planner if available, else create
        # one via the same provider selector so Intake and Planner stay in sync.
        llm_adapter: LLMAdapter | None = None
        if app.state.planner_service is not None:
            llm_adapter = app.state.planner_service._llm
        if llm_adapter is None:
            llm_adapter = LLMAdapterFactory.from_env()

        # Store LLM adapter on app.state for reuse by other components
        app.state.llm_adapter = llm_adapter

        # Local LLM (Ollama) for Intake — zero-cost, low-latency fast-path
        local_llm_adapter = None
        if os.environ.get("INTAKE_USE_LOCAL_LLM", "true").lower() in ("true", "1", "yes"):
            try:
                ollama_config = LLMConfig(
                    provider="ollama",
                    api_key=None,
                    timeout_s=int(os.environ.get("INTAKE_LOCAL_TIMEOUT_S", "30")),
                )
                local_llm_adapter = LLMAdapterFactory.create(ollama_config)
                logger.info("Ollama adapter created for Intake local LLM")
            except Exception as exc:
                logger.info("Ollama adapter not available, using remote only: %s", exc)
                local_llm_adapter = None

        app.state.intake_service = create_intake_service(
            redis_client=intake_redis,
            llm_adapter=llm_adapter,
            local_llm_adapter=local_llm_adapter,
        )

        # Wire Redis into PolicyEngine cache now that it's available
        from components.PolicyEngine.adapters.cache import PolicyCacheAdapter

        app.state.policy_service._cache = PolicyCacheAdapter(intake_redis)
    except Exception as exc:
        logger.warning("Intake init failed: %s", exc)
        app.state.intake_service = None
        if not hasattr(app.state, "llm_adapter"):
            app.state.llm_adapter = None

    # Shared MCP client adapter (one instance for both orchestrators)
    from components.ExecuteOrchestrator.adapters.mcp_client import MCPClientAdapter

    mcp_client = MCPClientAdapter(
        config=mcp_config,
        http_client=mcp_http_client,
        session_manager=mcp_session_manager,
        composio_config=composio_config,
        url_manager=url_manager,
    )

    # IntegrationManager (user-provider connection status)
    try:
        from components.IntegrationManager.adapters.connection_cache import (
            ConnectionCache,
        )
        from components.IntegrationManager.adapters.db import IntegrationDatabaseAdapter
        from components.IntegrationManager.service.integration_service import (
            IntegrationManager,
        )

        integration_db = IntegrationDatabaseAdapter()
        connection_cache = ConnectionCache(intake_redis) if intake_redis is not None else None

        composio_client = None
        if composio_config is not None:
            from components.IntegrationManager.adapters.composio_client import (
                ComposioClient,
            )

            composio_client = ComposioClient(
                http_client=mcp_http_client,
                api_key=composio_config.api_key,
                base_url=composio_config.base_url,
            )

        app.state.integration_manager = IntegrationManager(
            db_adapter=integration_db,
            composio_config=composio_config,
            composio_client=composio_client,
            connection_cache=connection_cache,
        )
    except Exception as exc:
        logger.warning("IntegrationManager init failed: %s", exc)
        app.state.integration_manager = None

    # Wire per-user tool cache into ToolCatalog (needs Redis from Intake block)
    if intake_redis is not None:
        app.state.tool_catalog._user_tool_cache = UserToolCache(intake_redis)

    # Wire ToolCatalog + IntegrationManager into Intake (for provider connection checks)
    if app.state.intake_service is not None:
        app.state.intake_service._tool_catalog = app.state.tool_catalog
        app.state.intake_service._integration_manager = getattr(
            app.state, "integration_manager", None
        )

    # TrustFilter service (library -- stateless sanitizer, graceful degradation)
    try:
        from components.TrustFilter.service.filter_service import (
            create_filter_service,
        )

        app.state.filter_service = create_filter_service()
    except Exception as exc:
        logger.warning("TrustFilter init failed: %s", exc)
        app.state.filter_service = None

    # ExecuteOrchestrator service (Orchestration Layer -- graceful degradation)
    try:
        from components.ExecuteOrchestrator.adapters.credential_vault import (
            CredentialVaultAdapter,
        )
        from components.ExecuteOrchestrator.adapters.llm_client import (
            create_reasoning_adapter,
        )
        from components.ExecuteOrchestrator.service.execute_service import (
            create_execute_service,
        )

        app.state.execute_service = create_execute_service(
            policy_service=app.state.policy_service,
            tool_catalog=app.state.tool_catalog,
            plan_writer_service=app.state.plan_writer_service,
            mcp_client=mcp_client,
            llm_client=create_reasoning_adapter(),
            credential_vault=CredentialVaultAdapter(db=db),
            redis_client=intake_redis,
            filter_service=app.state.filter_service,
        )
    except Exception as exc:
        logger.warning("ExecuteOrchestrator init failed: %s", exc)
        app.state.execute_service = None

    # PreviewOrchestrator service (library -- no routes, graceful degradation)
    try:
        from components.PreviewOrchestrator.service.preview_service import (
            create_preview_service,
        )

        app.state.preview_service = create_preview_service(
            mcp_client=mcp_client,
            tool_catalog=app.state.tool_catalog,
            redis_client=intake_redis,
        )
    except Exception as exc:
        logger.warning("PreviewOrchestrator init failed: %s", exc)
        app.state.preview_service = None

    # ApprovalGate service (library -- no routes, graceful degradation)
    try:
        from components.ApprovalGate.service.approval_service import (
            create_approval_service,
        )

        app.state.approval_service = create_approval_service(
            preview_service=app.state.preview_service,
            policy_service=app.state.policy_service,
            redis_client=intake_redis,
            jwt_secret=os.environ.get("APPROVAL_TOKEN_SECRET", ""),
            token_ttl_s=int(os.environ.get("APPROVAL_TOKEN_TTL_S", "900")),
        )
    except Exception as exc:
        logger.warning("ApprovalGate init failed: %s", exc)
        app.state.approval_service = None

    # ExecutionMonitor services (background watchdog, graceful degradation)
    monitor_task = None
    try:
        from components.ExecutionMonitor.adapters.notifier import LogNotifier
        from components.ExecutionMonitor.adapters.tracker_db import TrackerDatabaseAdapter
        from components.ExecutionMonitor.service.monitor_service import MonitorService
        from components.ExecutionMonitor.service.tracker_service import TrackerService

        tracker_db = TrackerDatabaseAdapter()
        app.state.tracker_service = TrackerService(tracker_db=tracker_db)
        app.state.monitor_service = MonitorService(
            tracker_db=tracker_db,
            notifier=LogNotifier(),
        )

        # Wire tracker into ExecuteOrchestrator
        if app.state.execute_service is not None:
            app.state.execute_service._tracker = app.state.tracker_service

        # Start background monitor task
        monitor_task = asyncio.create_task(
            app.state.monitor_service.run(), name="execution-monitor"
        )
    except Exception as exc:
        logger.warning("ExecutionMonitor init failed: %s", exc)
        app.state.tracker_service = None
        app.state.monitor_service = None

    # Audit service (platform layer — append-only audit log, graceful degradation)
    try:
        from components.Audit.adapters.db import AuditDatabaseAdapter
        from components.Audit.service.audit_service import AuditService

        audit_db = AuditDatabaseAdapter()
        app.state.audit_service = AuditService(db_adapter=audit_db)

        # Wire audit into upstream components
        if app.state.execute_service is not None:
            app.state.execute_service._audit = app.state.audit_service
        if app.state.approval_service is not None:
            app.state.approval_service._audit = app.state.audit_service
        if app.state.monitor_service is not None:
            app.state.monitor_service._audit = app.state.audit_service
    except Exception as exc:
        logger.warning("Audit init failed: %s", exc)
        app.state.audit_service = None

    # Scheduler service (APScheduler lifecycle + job execution)
    scheduler_service = None
    try:
        from components.Scheduler.adapters.db import SchedulerDatabaseAdapter
        from components.Scheduler.service.scheduler_service import SchedulerService

        scheduler_db = SchedulerDatabaseAdapter()
        scheduler_service = SchedulerService(
            db=scheduler_db,
            planner_service=app.state.planner_service,
            execute_service=app.state.execute_service,
            approval_service=app.state.approval_service,
            plan_service=app.state.plan_service,
        )
        await scheduler_service.start()
        app.state.scheduler_service = scheduler_service
    except Exception as exc:
        logger.warning("Scheduler init failed: %s", exc)
        app.state.scheduler_service = None

    logger.info("All services initialized")

    yield

    # --- Shutdown ---
    # Stop Scheduler
    if scheduler_service is not None:
        try:
            await scheduler_service.stop()
        except Exception as exc:
            logger.warning("Scheduler shutdown error: %s", exc)

    # Stop ExecutionMonitor background task
    if monitor_task is not None:
        try:
            app.state.monitor_service.stop()
            monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor_task
        except Exception as exc:
            logger.warning("ExecutionMonitor shutdown error: %s", exc)

    # Close MCP infrastructure
    if url_manager is not None:
        url_manager.invalidate_all()
    mcp_session_manager.invalidate_all()
    await mcp_http_client.aclose()

    if intake_redis is not None:
        await intake_redis.close()
    await db.close()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Personal Agent",
        description="AI-powered personal assistant with preview-first planning and tool orchestration",
        lifespan=lifespan,
    )

    # Middleware (order matters — last added = first executed)
    from shared.api.error_handlers import create_error_handler_middleware
    from shared.middleware.auth import AuthMiddleware

    app.middleware("http")(create_error_handler_middleware())
    app.add_middleware(AuthMiddleware)

    # CORS (useful for local dev when opening HTML directly)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    from components.History.api.routes import router as history_router
    from components.Intake.api.routes import router as intake_router
    from components.PlanLibrary.api.routes import router as plan_router
    from components.ProfileStore.api.routes import router as profile_router
    from shared.api.auth_routes import router as auth_router

    app.include_router(auth_router)
    app.include_router(plan_router)
    app.include_router(profile_router)
    app.include_router(history_router)
    app.include_router(intake_router)

    from components.ExecuteOrchestrator.api.routes import router as execute_router

    app.include_router(execute_router)

    from shared.api.orchestrate_routes import router as orchestrate_router

    app.include_router(orchestrate_router)

    from components.Audit.api.routes import router as audit_router
    from components.IntegrationManager.api.routes import router as integration_router

    app.include_router(audit_router)
    app.include_router(integration_router)

    from components.Scheduler.api.routes import router as scheduler_router

    app.include_router(scheduler_router)

    # Root health check
    @app.get("/health")
    async def root_health():
        return {"status": "ok", "service": "personal-agent"}

    # Serve static UI files
    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/")
        async def root_redirect():
            return FileResponse(str(static_dir / "index.html"))

    return app
