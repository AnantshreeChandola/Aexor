"""
Database Adapter for IntegrationManager

Async SQLAlchemy operations for the user_connections table.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, update

from shared.database.adapter import get_database_adapter
from shared.database.models import UserConnectionTable

from ..domain.models import UserConnection

logger = logging.getLogger(__name__)


def _row_to_model(row: UserConnectionTable) -> UserConnection:
    return UserConnection(
        user_id=str(row.user_id),
        provider_name=row.provider_name,
        is_connected=row.is_connected,
        connected_at=row.connected_at,
        disconnected_at=row.disconnected_at,
        composio_entity_id=row.composio_entity_id,
    )


class IntegrationDatabaseAdapter:
    """Database adapter for user_connections table."""

    def __init__(self) -> None:
        self.shared_db = get_database_adapter()

    async def get_connection(
        self,
        user_id: str,
        provider_name: str,
    ) -> UserConnection | None:
        async with self.shared_db.get_session() as session:
            stmt = select(UserConnectionTable).where(
                UserConnectionTable.user_id == user_id,
                UserConnectionTable.provider_name == provider_name,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _row_to_model(row) if row else None

    async def get_user_connections(
        self,
        user_id: str,
    ) -> list[UserConnection]:
        async with self.shared_db.get_session() as session:
            stmt = select(UserConnectionTable).where(
                UserConnectionTable.user_id == user_id,
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_model(r) for r in rows]

    async def is_user_connected(
        self,
        user_id: str,
        provider_name: str,
    ) -> bool:
        async with self.shared_db.get_session() as session:
            stmt = select(UserConnectionTable.is_connected).where(
                UserConnectionTable.user_id == user_id,
                UserConnectionTable.provider_name == provider_name,
            )
            result = (await session.execute(stmt)).scalar_one_or_none()
            return result is True

    async def upsert_connection(
        self,
        user_id: str,
        provider_name: str,
        is_connected: bool,
        composio_entity_id: str,
    ) -> UserConnection:
        """Insert or update a connection record."""
        now = datetime.now(UTC)
        async with self.shared_db.get_session() as session, session.begin():
            stmt = select(UserConnectionTable).where(
                UserConnectionTable.user_id == user_id,
                UserConnectionTable.provider_name == provider_name,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing:
                values: dict = {"is_connected": is_connected}
                if is_connected:
                    values["connected_at"] = now
                    values["disconnected_at"] = None
                else:
                    values["disconnected_at"] = now
                values["composio_entity_id"] = composio_entity_id

                upd = (
                    update(UserConnectionTable)
                    .where(
                        UserConnectionTable.user_id == user_id,
                        UserConnectionTable.provider_name == provider_name,
                    )
                    .values(**values)
                )
                await session.execute(upd)
            else:
                row = UserConnectionTable(
                    user_id=user_id,
                    provider_name=provider_name,
                    is_connected=is_connected,
                    connected_at=now if is_connected else None,
                    composio_entity_id=composio_entity_id,
                )
                session.add(row)

        return UserConnection(
            user_id=user_id,
            provider_name=provider_name,
            is_connected=is_connected,
            connected_at=now if is_connected else None,
            disconnected_at=now if not is_connected else None,
            composio_entity_id=composio_entity_id,
        )

    async def health_check(self) -> bool:
        return await self.shared_db.health_check()
