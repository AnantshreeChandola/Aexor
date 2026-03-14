"""
Shared Database Adapter

Common database initialization and connection management.
Used across all components that need database access.
"""

import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

logger = logging.getLogger(__name__)

# Global base for SQLAlchemy models
Base = declarative_base()


class DatabaseConfig:
    """Database configuration management."""

    @staticmethod
    def get_database_url(database_url: str | None = None) -> str:
        """
        Get database URL with proper async driver.

        Args:
            database_url: Database URL. If None, reads from DATABASE_URL env var.

        Returns:
            Database URL with async driver

        Raises:
            ValueError: If DATABASE_URL not found
        """
        if database_url is None:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise ValueError("DATABASE_URL environment variable not set")

        # Convert postgresql:// to postgresql+asyncpg:// for async support
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://")

        return database_url


class SharedDatabaseAdapter:
    """
    Shared database adapter for all components.

    Provides common database initialization, connection management,
    and session handling that can be reused across components.
    """

    def __init__(self, database_url: str | None = None):
        """
        Initialize shared database adapter.

        Args:
            database_url: PostgreSQL connection string
                         If None, reads from DATABASE_URL environment variable
        """
        self.database_url = DatabaseConfig.get_database_url(database_url)

        self.engine = create_async_engine(
            self.database_url,
            echo=False,  # Set to True for SQL debugging
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,  # Validate connections before use
            pool_recycle=3600,  # Recycle connections after 1 hour
        )

        self.async_session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        logger.info("Shared database adapter initialized")

    def get_session(self) -> AsyncSession:
        """
        Get async database session as context manager.

        Usage:
            async with adapter.get_session() as session:
                ...

        Returns:
            AsyncSession (async context manager)
        """
        return self.async_session()

    async def health_check(self) -> bool:
        """
        Check database connectivity.

        Returns:
            True if database is accessible, False otherwise
        """
        try:
            async with self.async_session() as session:
                await session.execute("SELECT 1")
                return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False

    async def close(self):
        """Close database connections."""
        await self.engine.dispose()
        logger.info("Database connections closed")


# Singleton instance
_shared_db_adapter = None


def get_database_adapter() -> SharedDatabaseAdapter:
    """
    Get singleton database adapter instance.

    Returns:
        SharedDatabaseAdapter: Shared instance
    """
    global _shared_db_adapter
    if _shared_db_adapter is None:
        _shared_db_adapter = SharedDatabaseAdapter()
    return _shared_db_adapter
