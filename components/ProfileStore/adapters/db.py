"""
Database Adapter for ProfileStore

Async SQLAlchemy 2.0 operations for preferences table.
Handles database connections, transactions, and CRUD operations.

Reference: LLD.md §6.1
"""

import logging
from typing import Any, Optional
from uuid import UUID
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import (
    Column, String, Boolean, DateTime, UUID as SQLAlchemy_UUID, 
    Index, text, select, insert, update, delete as sql_delete
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base
from sqlalchemy.exc import IntegrityError
import os

from ..domain.models import PreferenceDB, UserNotFoundError

logger = logging.getLogger(__name__)

Base = declarative_base()


class PreferenceTable(Base):
    """SQLAlchemy model for preferences table."""
    __tablename__ = "preferences"
    
    preference_id = Column(
        SQLAlchemy_UUID(as_uuid=True), 
        primary_key=True, 
        server_default=text("gen_random_uuid()")
    )
    user_id = Column(SQLAlchemy_UUID(as_uuid=True), nullable=False)
    key = Column(String(64), nullable=False)
    value = Column(JSONB, nullable=False)
    sensitive = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, server_default=text("NOW()"))
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "idx_preferences_user_key_active", 
            user_id, key, 
            unique=True,
            postgresql_where=deleted_at.is_(None)
        ),
        Index(
            "idx_preferences_user_id", 
            user_id,
            postgresql_where=deleted_at.is_(None)
        ),
        Index("idx_preferences_deleted_at", deleted_at),
    )


class DatabaseAdapter:
    """
    Async database adapter for ProfileStore operations.
    
    Provides CRUD operations for preferences with proper transaction handling.
    Uses connection pooling and async SQLAlchemy 2.0.
    """
    
    def __init__(self, database_url: str = None):
        """
        Initialize database adapter.
        
        Args:
            database_url: PostgreSQL connection string
                         If None, reads from DATABASE_URL environment variable
        """
        if database_url is None:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise ValueError("DATABASE_URL environment variable not set")
        
        # Convert postgresql:// to postgresql+asyncpg:// for async support
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://")
        
        self.engine = create_async_engine(
            database_url,
            echo=False,  # Set to True for SQL debugging
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,  # Validate connections before use
        )
        
        self.async_session = async_sessionmaker(
            self.engine, 
            class_=AsyncSession,
            expire_on_commit=False
        )
        
        logger.info(f"Database adapter initialized with connection pool")

    async def get_preference(
        self, 
        user_id: UUID, 
        preference_key: str
    ) -> PreferenceDB | None:
        """
        Retrieve a preference from database.
        
        Args:
            user_id: User UUID
            preference_key: Preference key
            
        Returns:
            PreferenceDB model if found, None if not found
            
        Raises:
            UserNotFoundError: If user_id doesn't exist in users table
        """
        async with self.async_session() as session:
            try:
                # Check if user exists first
                user_check = await session.execute(
                    text("SELECT 1 FROM users WHERE user_id = :user_id AND deleted_at IS NULL"),
                    {"user_id": user_id}
                )
                if not user_check.fetchone():
                    raise UserNotFoundError(user_id)
                
                # Query for preference
                stmt = select(PreferenceTable).where(
                    PreferenceTable.user_id == user_id,
                    PreferenceTable.key == preference_key,
                    PreferenceTable.deleted_at.is_(None)
                )
                
                result = await session.execute(stmt)
                preference = result.scalar_one_or_none()
                
                if preference is None:
                    return None
                
                return PreferenceDB(
                    preference_id=preference.preference_id,
                    user_id=preference.user_id,
                    key=preference.key,
                    value=preference.value,
                    sensitive=preference.sensitive,
                    updated_at=preference.updated_at,
                    deleted_at=preference.deleted_at
                )
                
            except Exception as e:
                logger.error(f"Database error getting preference: {e}")
                raise

    async def upsert_preference(
        self,
        user_id: UUID,
        preference_key: str,
        value: Any,
        sensitive: bool = False
    ) -> PreferenceDB:
        """
        Insert or update a preference (upsert).
        
        Args:
            user_id: User UUID
            preference_key: Preference key
            value: Preference value (JSON-serializable)
            sensitive: Whether the preference is sensitive
            
        Returns:
            PreferenceDB model of the created/updated preference
            
        Raises:
            UserNotFoundError: If user_id doesn't exist in users table
        """
        async with self.async_session() as session:
            try:
                # Check if user exists first
                user_check = await session.execute(
                    text("SELECT 1 FROM users WHERE user_id = :user_id AND deleted_at IS NULL"),
                    {"user_id": user_id}
                )
                if not user_check.fetchone():
                    raise UserNotFoundError(user_id)
                
                # Use PostgreSQL UPSERT (INSERT ... ON CONFLICT)
                stmt = text("""
                    INSERT INTO preferences (user_id, key, value, sensitive, updated_at)
                    VALUES (:user_id, :key, :value, :sensitive, NOW())
                    ON CONFLICT (user_id, key) WHERE deleted_at IS NULL
                    DO UPDATE SET 
                        value = EXCLUDED.value,
                        sensitive = EXCLUDED.sensitive,
                        updated_at = NOW(),
                        deleted_at = NULL
                    RETURNING preference_id, user_id, key, value, sensitive, updated_at, deleted_at
                """)
                
                result = await session.execute(stmt, {
                    "user_id": user_id,
                    "key": preference_key,
                    "value": value,
                    "sensitive": sensitive
                })
                
                row = result.fetchone()
                if not row:
                    raise RuntimeError("Upsert operation failed to return result")
                
                await session.commit()
                
                return PreferenceDB(
                    preference_id=row.preference_id,
                    user_id=row.user_id,
                    key=row.key,
                    value=row.value,
                    sensitive=row.sensitive,
                    updated_at=row.updated_at,
                    deleted_at=row.deleted_at
                )
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Database error upserting preference: {e}")
                raise

    async def delete_preference(
        self,
        user_id: UUID,
        preference_key: str
    ) -> bool:
        """
        Soft delete a preference (set deleted_at timestamp).
        
        Args:
            user_id: User UUID
            preference_key: Preference key
            
        Returns:
            True if preference was deleted, False if not found
            
        Raises:
            UserNotFoundError: If user_id doesn't exist in users table
        """
        async with self.async_session() as session:
            try:
                # Check if user exists first
                user_check = await session.execute(
                    text("SELECT 1 FROM users WHERE user_id = :user_id AND deleted_at IS NULL"),
                    {"user_id": user_id}
                )
                if not user_check.fetchone():
                    raise UserNotFoundError(user_id)
                
                # Soft delete the preference
                stmt = text("""
                    UPDATE preferences 
                    SET deleted_at = NOW() 
                    WHERE user_id = :user_id 
                      AND key = :key 
                      AND deleted_at IS NULL
                """)
                
                result = await session.execute(stmt, {
                    "user_id": user_id,
                    "key": preference_key
                })
                
                await session.commit()
                
                # Return True if any rows were affected
                return result.rowcount > 0
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Database error deleting preference: {e}")
                raise

    async def get_all_preferences(
        self, 
        user_id: UUID
    ) -> list[PreferenceDB]:
        """
        Get all preferences for a user.
        
        Args:
            user_id: User UUID
            
        Returns:
            List of PreferenceDB models
            
        Raises:
            UserNotFoundError: If user_id doesn't exist in users table
        """
        async with self.async_session() as session:
            try:
                # Check if user exists first
                user_check = await session.execute(
                    text("SELECT 1 FROM users WHERE user_id = :user_id AND deleted_at IS NULL"),
                    {"user_id": user_id}
                )
                if not user_check.fetchone():
                    raise UserNotFoundError(user_id)
                
                # Query for all active preferences
                stmt = select(PreferenceTable).where(
                    PreferenceTable.user_id == user_id,
                    PreferenceTable.deleted_at.is_(None)
                ).order_by(PreferenceTable.key)
                
                result = await session.execute(stmt)
                preferences = result.scalars().all()
                
                return [
                    PreferenceDB(
                        preference_id=pref.preference_id,
                        user_id=pref.user_id,
                        key=pref.key,
                        value=pref.value,
                        sensitive=pref.sensitive,
                        updated_at=pref.updated_at,
                        deleted_at=pref.deleted_at
                    )
                    for pref in preferences
                ]
                
            except Exception as e:
                logger.error(f"Database error getting all preferences: {e}")
                raise

    async def health_check(self) -> bool:
        """
        Check database connectivity.
        
        Returns:
            True if database is accessible, False otherwise
        """
        try:
            async with self.async_session() as session:
                await session.execute(text("SELECT 1"))
                return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False

    async def close(self):
        """Close database connections."""
        await self.engine.dispose()
        logger.info("Database connections closed")