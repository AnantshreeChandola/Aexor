"""
PlanLibrary Component Entry Point

Main module for running PlanLibrary as a standalone service
or integrating with larger application.
"""

import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .api.routes import router
from .api.dependencies import (
    get_plan_service, get_vector_service, get_analytics_service
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifespan events.
    
    Handles startup and shutdown procedures for PlanLibrary services.
    """
    # Startup
    logger.info("Starting PlanLibrary component")
    
    try:
        # Initialize services (dependency injection handles this)
        plan_service = get_plan_service()
        vector_service = get_vector_service()
        analytics_service = get_analytics_service()
        
        # Perform startup health checks
        plan_health = await plan_service.health_check()
        vector_health = await vector_service.health_check()
        analytics_health = await analytics_service.health_check()
        
        logger.info(f"Service health on startup:")
        logger.info(f"  Plan service: {'healthy' if plan_health.get('status') == 'healthy' else 'unhealthy'}")
        logger.info(f"  Vector service: {'healthy' if vector_health else 'unhealthy'}")
        logger.info(f"  Analytics service: {'healthy' if analytics_health else 'unhealthy'}")
        
        yield
        
    except Exception as e:
        logger.error(f"Startup error: {e}")
        raise
        
    finally:
        # Shutdown
        logger.info("Shutting down PlanLibrary component")
        
        try:
            # Cleanup vector service background tasks
            vector_service = get_vector_service()
            await vector_service.close()
            
            logger.info("PlanLibrary shutdown complete")
            
        except Exception as e:
            logger.error(f"Shutdown error: {e}")


def create_app() -> FastAPI:
    """
    Create FastAPI application with PlanLibrary routes.
    
    Returns:
        FastAPI application instance
    """
    app = FastAPI(
        title="PlanLibrary",
        description="Memory layer component for plan storage and retrieval",
        version="1.0.0",
        lifespan=lifespan
    )
    
    # Include PlanLibrary routes
    app.include_router(router)
    
    # Add root health check
    @app.get("/health")
    async def root_health_check() -> Dict[str, Any]:
        """Root level health check."""
        try:
            plan_service = get_plan_service()
            health = await plan_service.health_check()
            
            return {
                "service": "PlanLibrary",
                "status": "healthy" if health.get("status") == "healthy" else "unhealthy",
                "component": "Memory Layer",
                "version": "1.0.0"
            }
        except Exception as e:
            return {
                "service": "PlanLibrary", 
                "status": "unhealthy",
                "error": str(e),
                "component": "Memory Layer",
                "version": "1.0.0"
            }
    
    # Add error handlers
    @app.exception_handler(500)
    async def internal_server_error_handler(request, exc):
        """Handle internal server errors."""
        logger.error(f"Internal server error: {exc}")
        
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error_code": "INTERNAL_SERVER_ERROR",
                "message": "An internal server error occurred",
                "details": {}
            }
        )
    
    @app.exception_handler(404)
    async def not_found_handler(request, exc):
        """Handle not found errors."""
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "error_code": "NOT_FOUND",
                "message": "Resource not found",
                "details": {"path": str(request.url)}
            }
        )
    
    return app


# Create app instance
app = create_app()


async def main():
    """
    Run PlanLibrary component standalone.
    
    For development and testing purposes.
    """
    import uvicorn
    
    logger.info("Running PlanLibrary standalone")
    
    config = uvicorn.Config(
        "components.PlanLibrary.main:app",
        host="0.0.0.0",
        port=8001,
        log_level="info",
        reload=False
    )
    
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())