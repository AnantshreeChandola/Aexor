"""
PolicyEngine Service

Core policy evaluation logic: evaluate spawn requests against policy rules,
create attestation records, and manage policies with cache-first lookups.

Reference: GLOBAL_SPEC §2.9, §2.4.1
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import ulid

from shared.schemas.policy import PolicyAttestation, PolicyDecision, PolicyRule

from ..adapters.cache import PolicyCacheAdapter
from ..adapters.db import PolicyDatabaseAdapter
from ..domain.models import (
    AttestationError,
    PolicyAttestationDB,
    PolicyDB,
    SpawnRequest,
)

logger = logging.getLogger(__name__)

# Absolute hard caps from GLOBAL_SPEC
_MAX_STEPS_PER_SPAWN = 10  # abs max per single spawn operation
_MAX_TOTAL_PLAN_STEPS = 100  # abs max steps in a plan


class PolicyService:
    """Core PolicyEngine service.

    Evaluates spawn requests against policy rules, with deny-by-default
    semantics. Uses cache-first lookups with DB fallback.
    """

    def __init__(
        self,
        db_adapter: PolicyDatabaseAdapter,
        cache_adapter: PolicyCacheAdapter,
    ) -> None:
        self._db = db_adapter
        self._cache = cache_adapter

    # ------------------------------------------------------------------
    # Spawn evaluation
    # ------------------------------------------------------------------

    async def evaluate_spawn(self, request: SpawnRequest) -> PolicyDecision:
        """Evaluate whether a step may spawn child steps.

        Resolution order: explicit policy_ref → role-default → system-default.
        If no policy is found, deny-by-default applies.

        Args:
            request: The spawn evaluation request.

        Returns:
            PolicyDecision with allowed/denied result and reason.
        """
        logger.info(
            "evaluate_spawn: plan_id=%s step=%d proposed=%d policy_ref=%s",
            request.plan_id,
            request.spawning_step,
            len(request.proposed_steps),
            request.policy_ref,
        )

        # 1. Resolve policy
        rule: PolicyRule | None = None
        if request.policy_ref:
            rule = await self.get_policy(request.policy_ref)

        if rule is None:
            logger.info(
                "No matching policy for plan_id=%s step=%d — deny-by-default",
                request.plan_id,
                request.spawning_step,
            )
            return PolicyDecision(
                allowed=False,
                reason="No matching policy found — deny-by-default",
            )

        # 2. Evaluate proposed steps against the policy
        violations: list[str] = []
        requires_approval = rule.require_approval

        # Check total count limits
        proposed_count = len(request.proposed_steps)
        if proposed_count > rule.max_spawned_steps:
            violations.append(
                f"Proposed {proposed_count} steps exceeds policy max_spawned_steps={rule.max_spawned_steps}"
            )
        if proposed_count > _MAX_STEPS_PER_SPAWN:
            violations.append(
                f"Proposed {proposed_count} steps exceeds absolute max per spawn ({_MAX_STEPS_PER_SPAWN})"
            )

        new_total = request.current_step_count + proposed_count
        if new_total > _MAX_TOTAL_PLAN_STEPS:
            violations.append(
                f"New total {new_total} steps exceeds absolute plan limit ({_MAX_TOTAL_PLAN_STEPS})"
            )

        for step_dict in request.proposed_steps:
            step_num = step_dict.get("step", "?")

            # No recursive spawning
            if step_dict.get("can_spawn", False):
                violations.append(
                    f"Step {step_num}: recursive spawning not allowed (can_spawn=true on spawned step)"
                )

            # Role check
            role = step_dict.get("role", "")
            if rule.allowed_roles and role not in rule.allowed_roles:
                violations.append(
                    f"Step {step_num}: role '{role}' not in allowed_roles {rule.allowed_roles}"
                )

            # Booker HITL enforcement (non-overridable)
            if role == "Booker":
                requires_approval = True

            # Tool check
            tool_id = step_dict.get("uses", "")
            if "*" not in rule.allowed_tools and tool_id not in rule.allowed_tools:
                violations.append(f"Step {step_num}: tool '{tool_id}' not in allowed_tools")

            # Plugin constraint check
            if request.plan_plugins and tool_id not in request.plan_plugins:
                violations.append(
                    f"Step {step_num}: tool '{tool_id}' not in plan plugins {request.plan_plugins}"
                )

            # Forbidden actions check
            call = step_dict.get("call", "")
            if call in rule.forbidden_actions:
                violations.append(f"Step {step_num}: call '{call}' is in forbidden_actions")

        if violations:
            reason = "; ".join(violations)
            logger.info(
                "evaluate_spawn DENIED: plan_id=%s policy_id=%s violations=%s",
                request.plan_id,
                rule.policy_id,
                reason,
            )
            return PolicyDecision(
                allowed=False,
                requires_approval=False,
                reason=reason,
                violations=violations,
            )

        logger.info(
            "evaluate_spawn ALLOWED: plan_id=%s policy_id=%s requires_approval=%s",
            request.plan_id,
            rule.policy_id,
            requires_approval,
        )
        return PolicyDecision(
            allowed=True,
            requires_approval=requires_approval,
            reason=f"Approved by policy '{rule.policy_id}' v{rule.version}",
        )

    # ------------------------------------------------------------------
    # Attestation management
    # ------------------------------------------------------------------

    async def create_attestation(
        self,
        plan_id: str,
        plan_revision: int,
        spawned_by_step: int,
        new_steps: list[dict],
        policy_id: str,
        policy_version: int,
        decision: PolicyDecision,
    ) -> PolicyAttestation:
        """Create and store an attestation record for a spawn decision.

        Args:
            plan_id: Plan the spawn belongs to.
            plan_revision: Revision number of the plan.
            spawned_by_step: Step that triggered the spawn.
            new_steps: Serialized PlanStep dicts for the spawned steps.
            policy_id: Policy used for evaluation.
            policy_version: Version of the policy used.
            decision: The evaluation result.

        Returns:
            PolicyAttestation with a unique ULID.

        Raises:
            AttestationError: If storage fails.
        """
        attestation_id = str(ulid.new())
        now = datetime.now(UTC).isoformat()

        attestation = PolicyAttestation(
            attestation_id=attestation_id,
            plan_id=plan_id,
            plan_revision=plan_revision,
            spawned_by_step=spawned_by_step,
            new_steps=new_steps,
            policy_id=policy_id,
            policy_version=policy_version,
            decision=decision,
            attested_at=now,
        )

        db_model = PolicyAttestationDB(
            attestation_id=attestation_id,
            plan_id=plan_id,
            plan_revision=plan_revision,
            spawned_by_step=spawned_by_step,
            new_steps=new_steps,
            policy_id=policy_id,
            policy_version=policy_version,
            decision=decision.model_dump(),
            attested_at=datetime.now(UTC),
        )

        try:
            await self._db.store_attestation(db_model)
        except Exception as exc:
            raise AttestationError(f"Failed to store attestation: {exc}") from exc

        logger.info(
            "Attestation created: id=%s plan_id=%s policy_id=%s",
            attestation_id,
            plan_id,
            policy_id,
        )
        return attestation

    # ------------------------------------------------------------------
    # Policy CRUD
    # ------------------------------------------------------------------

    async def get_policy(self, policy_id: str, version: int | None = None) -> PolicyRule | None:
        """Retrieve a policy — cache-first with DB fallback.

        Args:
            policy_id: The policy identifier.
            version: Specific version, or None for latest.

        Returns:
            PolicyRule if found, None otherwise.
        """
        # Cache lookup (only if version is specified)
        if version is not None:
            cached = await self._cache.get_policy(policy_id, version)
            if cached is not None:
                return cached

        # DB lookup
        db_policy = await self._db.get_policy(policy_id, version)
        if db_policy is None:
            return None

        rule = PolicyRule(
            policy_id=db_policy.policy_id,
            name=db_policy.name,
            version=db_policy.version,
            scope=db_policy.scope,
            allowed_tools=db_policy.allowed_tools,
            allowed_roles=db_policy.allowed_roles,
            max_spawned_steps=db_policy.max_spawned_steps,
            require_approval=db_policy.require_approval,
            data_access=db_policy.data_access,
            forbidden_actions=db_policy.forbidden_actions,
            token_budget=db_policy.token_budget,
        )

        # Populate cache (best-effort; cache adapter itself handles errors)
        try:
            await self._cache.set_policy(policy_id, rule.version, rule)
        except Exception:
            logger.warning("Cache write failed for policy %s:%d", policy_id, rule.version)
        return rule

    async def create_policy(self, rule: PolicyRule) -> PolicyRule:
        """Store a new policy (or update existing). Invalidates cache.

        Args:
            rule: The policy rule to store.

        Returns:
            The stored PolicyRule.
        """
        db_model = PolicyDB(
            policy_id=rule.policy_id,
            name=rule.name,
            version=rule.version,
            scope=rule.scope,
            allowed_tools=rule.allowed_tools,
            allowed_roles=rule.allowed_roles,
            max_spawned_steps=rule.max_spawned_steps,
            require_approval=rule.require_approval,
            data_access=rule.data_access,
            forbidden_actions=rule.forbidden_actions,
            token_budget=rule.token_budget,
        )
        await self._db.store_policy(db_model)
        await self._cache.invalidate(rule.policy_id, rule.version)
        logger.info("Policy stored: id=%s v%d", rule.policy_id, rule.version)
        return rule

    async def list_policies(self, scope: str | None = None) -> list[PolicyRule]:
        """List all policies, optionally filtered by scope.

        Args:
            scope: Optional filter (step, role, system).

        Returns:
            List of PolicyRule instances.
        """
        db_policies = await self._db.list_policies(scope)
        return [
            PolicyRule(
                policy_id=p.policy_id,
                name=p.name,
                version=p.version,
                scope=p.scope,
                allowed_tools=p.allowed_tools,
                allowed_roles=p.allowed_roles,
                max_spawned_steps=p.max_spawned_steps,
                require_approval=p.require_approval,
                data_access=p.data_access,
                forbidden_actions=p.forbidden_actions,
                token_budget=p.token_budget,
            )
            for p in db_policies
        ]


def create_policy_service(
    db_adapter: PolicyDatabaseAdapter,
    redis_client: object | None = None,
) -> PolicyService:
    """Factory function for PolicyService.

    Args:
        db_adapter: PolicyEngine database adapter.
        redis_client: Optional async Redis client for caching.

    Returns:
        Configured PolicyService instance.
    """
    cache_adapter = PolicyCacheAdapter(redis_client)
    return PolicyService(db_adapter=db_adapter, cache_adapter=cache_adapter)
