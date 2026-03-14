"""
Database Adapter for PluginRegistry

Async SQLAlchemy 2.0 operations for tools, operations, and
registry_versions tables. Uses shared database utilities.

Reference: LLD.md Section 6.1
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update

from shared.database.adapter import get_database_adapter
from shared.database.models import (
    OperationTable,
    RegistryVersionTable,
    ToolTable,
)

from ..domain.models import (
    CreateToolRequest,
    OperationModel,
    ToolModel,
    UpdateToolRequest,
)

logger = logging.getLogger(__name__)


def _row_to_tool(
    tool_row: ToolTable,
    op_rows: list[OperationTable],
) -> ToolModel:
    """Convert DB rows into a ToolModel."""
    operations: dict[str, OperationModel] = {}
    for op in op_rows:
        operations[op.operation_id] = OperationModel(
            operation_id=op.operation_id,
            n8n_node=op.n8n_node,
            previewable=op.previewable,
            idempotent=op.idempotent,
            scopes=list(op.scopes) if op.scopes else [],
            compensation=op.compensation,
        )
    return ToolModel(
        tool_id=tool_row.tool_id,
        display_name=tool_row.display_name,
        credential_template=tool_row.credential_template,
        n8n_credential_type=tool_row.n8n_credential_type,
        active=tool_row.active,
        operations=operations,
        created_at=tool_row.created_at,
        updated_at=tool_row.updated_at,
    )


class RegistryDatabaseAdapter:
    """PluginRegistry database adapter using shared infrastructure."""

    def __init__(self) -> None:
        self.shared_db = get_database_adapter()
        logger.info("PluginRegistry database adapter initialized")

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    async def get_tool(self, tool_id: str) -> ToolModel | None:
        """Retrieve a tool with all its operations."""
        async with self.shared_db.get_session() as session:
            tool_row = await session.get(ToolTable, tool_id)
            if tool_row is None:
                return None

            ops_stmt = select(OperationTable).where(OperationTable.tool_id == tool_id)
            result = await session.execute(ops_stmt)
            op_rows = list(result.scalars().all())
            return _row_to_tool(tool_row, op_rows)

    async def list_active_tools(
        self,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[ToolModel], int]:
        """Return paginated active tools and total count."""
        async with self.shared_db.get_session() as session:
            # Count
            count_stmt = (
                select(func.count()).select_from(ToolTable).where(ToolTable.active.is_(True))
            )
            total = (await session.execute(count_stmt)).scalar() or 0

            # Paginate
            offset = (page - 1) * page_size
            tools_stmt = (
                select(ToolTable)
                .where(ToolTable.active.is_(True))
                .order_by(ToolTable.tool_id)
                .offset(offset)
                .limit(page_size)
            )
            rows = (await session.execute(tools_stmt)).scalars().all()

            # Load operations for each tool in a single query
            tool_ids = [r.tool_id for r in rows]
            ops: list[OperationTable] = []
            if tool_ids:
                ops_stmt = select(OperationTable).where(OperationTable.tool_id.in_(tool_ids))
                ops = list((await session.execute(ops_stmt)).scalars().all())

            ops_by_tool: dict[str, list[OperationTable]] = {}
            for op in ops:
                ops_by_tool.setdefault(op.tool_id, []).append(op)

            tools = [_row_to_tool(r, ops_by_tool.get(r.tool_id, [])) for r in rows]
            return tools, total

    async def get_tools_by_ids(
        self,
        tool_ids: list[str],
    ) -> dict[str, ToolModel]:
        """Retrieve tools by IDs (for validation). Returns a map."""
        async with self.shared_db.get_session() as session:
            stmt = select(ToolTable).where(ToolTable.tool_id.in_(tool_ids))
            rows = (await session.execute(stmt)).scalars().all()
            result: dict[str, ToolModel] = {}
            for row in rows:
                # Minimal model -- no operations needed for validation
                result[row.tool_id] = ToolModel(
                    tool_id=row.tool_id,
                    display_name=row.display_name,
                    credential_template=row.credential_template,
                    n8n_credential_type=row.n8n_credential_type,
                    active=row.active,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            return result

    async def tool_exists(self, tool_id: str) -> bool:
        """Check whether a tool_id exists."""
        async with self.shared_db.get_session() as session:
            row = await session.get(ToolTable, tool_id)
            return row is not None

    async def get_current_version(self) -> int:
        """Return MAX(version). Returns 0 if table is empty."""
        async with self.shared_db.get_session() as session:
            stmt = select(func.coalesce(func.max(RegistryVersionTable.version), 0))
            return (await session.execute(stmt)).scalar() or 0

    # ------------------------------------------------------------------
    # Write helpers (transactional)
    # ------------------------------------------------------------------

    async def create_tool(
        self,
        tool_def: CreateToolRequest,
    ) -> tuple[ToolModel, int]:
        """Insert tool + operations + version in one transaction."""
        now = datetime.now(UTC)
        async with self.shared_db.get_session() as session, session.begin():
            tool_row = ToolTable(
                tool_id=tool_def.tool_id,
                display_name=tool_def.display_name,
                credential_template=tool_def.credential_template,
                n8n_credential_type=tool_def.n8n_credential_type,
                active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(tool_row)

            for op_id, op in tool_def.operations.items():
                session.add(
                    OperationTable(
                        operation_id=op_id,
                        tool_id=tool_def.tool_id,
                        n8n_node=op.n8n_node,
                        previewable=op.previewable,
                        idempotent=op.idempotent,
                        scopes=list(op.scopes),
                        compensation=op.compensation,
                        created_at=now,
                    )
                )

            new_version = await self._increment_version(
                session,
                f"added {tool_def.tool_id}",
            )

        tool = await self.get_tool(tool_def.tool_id)
        return tool, new_version

    async def update_tool(
        self,
        tool_id: str,
        updates: UpdateToolRequest,
    ) -> tuple[ToolModel, int]:
        """Update tool metadata/operations + version in one txn."""
        now = datetime.now(UTC)
        async with self.shared_db.get_session() as session, session.begin():
            values: dict[str, object] = {"updated_at": now}
            if updates.display_name is not None:
                values["display_name"] = updates.display_name
            if updates.credential_template is not None:
                values["credential_template"] = updates.credential_template
            if updates.n8n_credential_type is not None:
                values["n8n_credential_type"] = updates.n8n_credential_type

            stmt = update(ToolTable).where(ToolTable.tool_id == tool_id).values(**values)
            await session.execute(stmt)

            # Replace operations if provided
            if updates.operations is not None:
                del_stmt = delete(OperationTable).where(OperationTable.tool_id == tool_id)
                await session.execute(del_stmt)
                for op_id, op in updates.operations.items():
                    session.add(
                        OperationTable(
                            operation_id=op_id,
                            tool_id=tool_id,
                            n8n_node=op.n8n_node,
                            previewable=op.previewable,
                            idempotent=op.idempotent,
                            scopes=list(op.scopes),
                            compensation=op.compensation,
                            created_at=now,
                        )
                    )

            new_version = await self._increment_version(
                session,
                f"updated {tool_id}",
            )

        tool = await self.get_tool(tool_id)
        return tool, new_version

    async def deactivate_tool(
        self,
        tool_id: str,
    ) -> tuple[ToolModel, int]:
        """Set active=false + version increment in one txn."""
        now = datetime.now(UTC)
        async with self.shared_db.get_session() as session, session.begin():
            stmt = (
                update(ToolTable)
                .where(ToolTable.tool_id == tool_id)
                .values(active=False, updated_at=now)
            )
            await session.execute(stmt)

            new_version = await self._increment_version(
                session,
                f"deactivated {tool_id}",
            )

        tool = await self.get_tool(tool_id)
        return tool, new_version

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _increment_version(
        self,
        session: object,
        change_summary: str,
    ) -> int:
        """Insert new version row inside an existing transaction."""
        cur = select(func.coalesce(func.max(RegistryVersionTable.version), 0))
        current = (await session.execute(cur)).scalar() or 0
        new_version = current + 1
        session.add(
            RegistryVersionTable(
                version=new_version,
                change_summary=change_summary,
            )
        )
        # Flush to ensure the version row is written
        await session.flush()
        return new_version

    async def health_check(self) -> bool:
        """Check database connectivity."""
        return await self.shared_db.health_check()
