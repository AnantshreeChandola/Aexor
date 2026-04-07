"""
Database Adapter for ProfileStore

Async SQLAlchemy 2.0 operations for preferences table.
Uses shared database utilities for connection management.

Reference: LLD.md §6.1
"""

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from shared.database.adapter import get_database_adapter
from shared.database.error_handler import with_db_error_handling, with_user_existence_check
from shared.database.models import PreferenceTable

from ..domain.models import PreferenceDB

logger = logging.getLogger(__name__)


class DatabaseAdapter:
    """
    ProfileStore database adapter.

    Uses shared database utilities for connection management.
    Provides CRUD operations for preferences with error handling.
    """

    def __init__(self):
        """Initialize database adapter using shared utilities."""
        self.shared_db = get_database_adapter()
        logger.info("ProfileStore database adapter initialized")

    @with_user_existence_check()
    @with_db_error_handling
    async def get_preference(self, user_id: UUID, preference_key: str) -> PreferenceDB | None:
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
        async with self.shared_db.get_session() as session:
            # Query for preference
            stmt = select(PreferenceTable).where(
                PreferenceTable.user_id == user_id,
                PreferenceTable.key == preference_key,
                PreferenceTable.deleted_at.is_(None),
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
                deleted_at=preference.deleted_at,
            )

    @with_user_existence_check()
    @with_db_error_handling
    async def upsert_preference(
        self, user_id: UUID, preference_key: str, value: Any, sensitive: bool = False
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
        async with self.shared_db.get_session() as session:
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

            result = await session.execute(
                stmt,
                {
                    "user_id": user_id,
                    "key": preference_key,
                    "value": json.dumps(value),
                    "sensitive": sensitive,
                },
            )

            row = result.fetchone()
            await session.commit()
            if not row:
                raise RuntimeError("Upsert operation failed to return result")

            return PreferenceDB(
                preference_id=row.preference_id,
                user_id=row.user_id,
                key=row.key,
                value=row.value,
                sensitive=row.sensitive,
                updated_at=row.updated_at,
                deleted_at=row.deleted_at,
            )

    @with_user_existence_check()
    @with_db_error_handling
    async def delete_preference(self, user_id: UUID, preference_key: str) -> bool:
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
        async with self.shared_db.get_session() as session:
            # Soft delete the preference
            stmt = text("""
                UPDATE preferences
                SET deleted_at = NOW()
                WHERE user_id = :user_id
                  AND key = :key
                  AND deleted_at IS NULL
            """)

            result = await session.execute(stmt, {"user_id": user_id, "key": preference_key})
            await session.commit()

            # Return True if any rows were affected
            return result.rowcount > 0

    @with_user_existence_check()
    @with_db_error_handling
    async def get_all_preferences(self, user_id: UUID) -> list[PreferenceDB]:
        """
        Get all preferences for a user.

        Args:
            user_id: User UUID

        Returns:
            List of PreferenceDB models

        Raises:
            UserNotFoundError: If user_id doesn't exist in users table
        """
        async with self.shared_db.get_session() as session:
            # Query for all active preferences
            stmt = (
                select(PreferenceTable)
                .where(PreferenceTable.user_id == user_id, PreferenceTable.deleted_at.is_(None))
                .order_by(PreferenceTable.key)
            )

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
                    deleted_at=pref.deleted_at,
                )
                for pref in preferences
            ]

    async def health_check(self) -> bool:
        """Check database connectivity using shared adapter."""
        return await self.shared_db.health_check()

    async def close(self):
        """Close database connections via shared adapter."""
        await self.shared_db.close()
