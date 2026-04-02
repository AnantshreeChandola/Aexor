"""
Database Adapter for PolicyEngine

Async SQLAlchemy 2.0 operations for policies and policy_attestations tables.
Uses shared database utilities.

Reference: GLOBAL_SPEC §2.9
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, update

from shared.database.adapter import get_database_adapter
from shared.database.models import PolicyAttestationTable, PolicyTable

from ..domain.models import PolicyAttestationDB, PolicyDB

logger = logging.getLogger(__name__)


def _row_to_policy(row: PolicyTable) -> PolicyDB:
    """Convert a PolicyTable row to a PolicyDB model."""
    return PolicyDB(
        policy_id=row.policy_id,
        name=row.name,
        version=row.version,
        scope=row.scope,
        allowed_tools=row.allowed_tools or ["*"],
        allowed_roles=row.allowed_roles or [],
        max_spawned_steps=row.max_spawned_steps,
        require_approval=row.require_approval,
        data_access=row.data_access or ["tier1"],
        forbidden_actions=row.forbidden_actions or [],
        token_budget=row.token_budget,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_attestation(row: PolicyAttestationTable) -> PolicyAttestationDB:
    """Convert a PolicyAttestationTable row to a PolicyAttestationDB model."""
    return PolicyAttestationDB(
        attestation_id=row.attestation_id,
        plan_id=row.plan_id,
        plan_revision=row.plan_revision,
        spawned_by_step=row.spawned_by_step,
        new_steps=row.new_steps,
        policy_id=row.policy_id,
        policy_version=row.policy_version,
        decision=row.decision,
        attested_at=row.attested_at,
    )


class PolicyDatabaseAdapter:
    """PolicyEngine database adapter using shared infrastructure."""

    def __init__(self) -> None:
        self.shared_db = get_database_adapter()
        logger.info("PolicyEngine database adapter initialized")

    # ------------------------------------------------------------------
    # Policy CRUD
    # ------------------------------------------------------------------

    async def store_policy(self, policy: PolicyDB) -> bool:
        """Insert or update a policy. Returns True on success."""
        now = datetime.now(UTC)
        async with self.shared_db.get_session() as session, session.begin():
            existing = await session.get(PolicyTable, policy.policy_id)
            if existing is not None:
                stmt = (
                    update(PolicyTable)
                    .where(PolicyTable.policy_id == policy.policy_id)
                    .values(
                        name=policy.name,
                        version=policy.version,
                        scope=policy.scope,
                        allowed_tools=policy.allowed_tools,
                        allowed_roles=policy.allowed_roles,
                        max_spawned_steps=policy.max_spawned_steps,
                        require_approval=policy.require_approval,
                        data_access=policy.data_access,
                        forbidden_actions=policy.forbidden_actions,
                        token_budget=policy.token_budget,
                        updated_at=now,
                    )
                )
                await session.execute(stmt)
            else:
                row = PolicyTable(
                    policy_id=policy.policy_id,
                    name=policy.name,
                    version=policy.version,
                    scope=policy.scope,
                    allowed_tools=policy.allowed_tools,
                    allowed_roles=policy.allowed_roles,
                    max_spawned_steps=policy.max_spawned_steps,
                    require_approval=policy.require_approval,
                    data_access=policy.data_access,
                    forbidden_actions=policy.forbidden_actions,
                    token_budget=policy.token_budget,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
        return True

    async def get_policy(self, policy_id: str, version: int | None = None) -> PolicyDB | None:
        """Retrieve a policy by ID, optionally at a specific version."""
        async with self.shared_db.get_session() as session:
            if version is not None:
                stmt = select(PolicyTable).where(
                    PolicyTable.policy_id == policy_id,
                    PolicyTable.version == version,
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
            else:
                row = await session.get(PolicyTable, policy_id)
            return _row_to_policy(row) if row else None

    async def list_policies(self, scope: str | None = None) -> list[PolicyDB]:
        """List policies, optionally filtered by scope."""
        async with self.shared_db.get_session() as session:
            stmt = select(PolicyTable).order_by(PolicyTable.policy_id)
            if scope is not None:
                stmt = stmt.where(PolicyTable.scope == scope)
            result = await session.execute(stmt)
            return [_row_to_policy(row) for row in result.scalars().all()]

    # ------------------------------------------------------------------
    # Attestation operations
    # ------------------------------------------------------------------

    async def store_attestation(self, attestation: PolicyAttestationDB) -> bool:
        """Insert a policy attestation record. Returns True on success."""
        async with self.shared_db.get_session() as session, session.begin():
            row = PolicyAttestationTable(
                attestation_id=attestation.attestation_id,
                plan_id=attestation.plan_id,
                plan_revision=attestation.plan_revision,
                spawned_by_step=attestation.spawned_by_step,
                new_steps=attestation.new_steps,
                policy_id=attestation.policy_id,
                policy_version=attestation.policy_version,
                decision=attestation.decision,
                attested_at=attestation.attested_at,
            )
            session.add(row)
        return True

    async def get_attestations_for_plan(self, plan_id: str) -> list[PolicyAttestationDB]:
        """Retrieve all attestations for a given plan."""
        async with self.shared_db.get_session() as session:
            stmt = (
                select(PolicyAttestationTable)
                .where(PolicyAttestationTable.plan_id == plan_id)
                .order_by(PolicyAttestationTable.attested_at)
            )
            result = await session.execute(stmt)
            return [_row_to_attestation(row) for row in result.scalars().all()]

    async def health_check(self) -> bool:
        """Check database connectivity."""
        return await self.shared_db.health_check()
