"""
Shared Database Models

SQLAlchemy models for core entities used across components.
These models represent the actual database tables.
"""

from sqlalchemy import UUID as SQLAlchemy_UUID
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover
    Vector = None  # pgvector not installed; VectorIndex degrades gracefully

from .adapter import Base


class UserTable(Base):
    """
    Users table - core identity for all components.

    Owned by Auth/Registration component but referenced by others.
    """

    __tablename__ = "users"

    user_id = Column(
        SQLAlchemy_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email = Column(String(255), unique=True, nullable=False)
    full_name = Column(String(255), nullable=True)
    password_hash = Column(String(255), nullable=True)
    context_tier = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime, nullable=False, server_default=text("NOW()"))
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_users_email", email),
        Index("idx_users_context_tier", context_tier),
        Index("idx_users_active", user_id, postgresql_where=deleted_at.is_(None)),
    )


class PreferenceTable(Base):
    """
    Preferences table - user preference storage.

    Owned by ProfileStore component.
    """

    __tablename__ = "preferences"

    preference_id = Column(
        SQLAlchemy_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    key = Column(String(64), nullable=False)
    value = Column(JSONB, nullable=False)
    sensitive = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, server_default=text("NOW()"))
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "idx_preferences_user_key_active",
            user_id,
            key,
            unique=True,
            postgresql_where=deleted_at.is_(None),
        ),
        Index("idx_preferences_user_id", user_id, postgresql_where=deleted_at.is_(None)),
        Index("idx_preferences_deleted_at", deleted_at),
    )


# PlanLibrary Tables - Owned by PlanLibrary component


class PlanTable(Base):
    """
    Plans table - stores executed plans with signatures.

    Owned by PlanLibrary component.
    """

    __tablename__ = "plans"

    plan_id = Column(String(26), primary_key=True)  # ULID format
    canonical_json = Column(JSONB, nullable=False)
    signature_data = Column(JSONB, nullable=False)
    intent_type = Column(String(64), nullable=False)
    step_count = Column(Integer, nullable=False)
    plan_hash = Column(String(64), nullable=False)  # SHA-256 hex
    size_bytes = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False)
    stored_at = Column(DateTime, nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_plans_intent_type", intent_type),
        Index("idx_plans_stored_at", stored_at),
        Index("idx_plans_hash", plan_hash),
        Index("idx_plans_step_count", step_count),
    )


class PlanOutcomeTable(Base):
    """
    Plan outcomes table - stores execution results.

    Owned by PlanLibrary component.
    """

    __tablename__ = "plan_outcomes"

    outcome_id = Column(
        SQLAlchemy_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    plan_id = Column(String(26), ForeignKey("plans.plan_id", ondelete="CASCADE"), nullable=False)
    success = Column(Boolean, nullable=False)
    error_type = Column(String(64), nullable=True)
    error_details = Column(JSONB, nullable=True)
    execution_start = Column(DateTime, nullable=False)
    execution_end = Column(DateTime, nullable=False)
    total_steps = Column(Integer, nullable=False)
    failed_step = Column(Integer, nullable=True)
    context_data = Column(JSONB, nullable=True)
    final_graph_json = Column(JSONB, nullable=True)
    plan_revision = Column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (
        Index("idx_plan_outcomes_plan_id", plan_id),
        Index("idx_plan_outcomes_success", success),
        Index("idx_plan_outcomes_execution_start", execution_start),
    )


class PlanEmbeddingTable(Base):
    """
    Plan embeddings table - stores vector embeddings and tsvector for hybrid search.

    Owned by VectorIndex component. Requires pgvector extension.
    """

    __tablename__ = "plan_embeddings"

    embedding_id = Column(
        SQLAlchemy_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    plan_id = Column(
        String(26),
        ForeignKey("plans.plan_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # One embedding per plan
    )
    intent_type = Column(String(64), nullable=False, default="unknown")
    embedding = Column(
        Vector(384) if Vector is not None else String,
        nullable=False,
    )
    search_text = Column(String, nullable=False)
    tsv = Column(TSVECTOR, nullable=True)
    model_version = Column(String(32), nullable=False, default="all-MiniLM-L6-v2")
    created_at = Column(DateTime, nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_plan_embeddings_plan_id", plan_id),
        Index("idx_plan_embeddings_intent_type", intent_type),
        Index("idx_plan_embeddings_tsv", tsv, postgresql_using="gin"),
        Index(
            "idx_plan_embeddings_hnsw",
            embedding,
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("idx_plan_embeddings_created_at", created_at),
    )


class PlanMetricsTable(Base):
    """
    Plan metrics table - stores performance metrics.

    Owned by PlanLibrary component.
    """

    __tablename__ = "plan_metrics"

    metrics_id = Column(
        SQLAlchemy_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    plan_id = Column(String(26), ForeignKey("plans.plan_id", ondelete="CASCADE"), nullable=False)
    preview_latency_ms = Column(Integer, nullable=True)
    execute_latency_ms = Column(Integer, nullable=False)
    step_timings = Column(JSONB, nullable=True)
    resource_usage = Column(JSONB, nullable=True)

    __table_args__ = (
        Index("idx_plan_metrics_plan_id", plan_id),
        Index("idx_plan_metrics_execute_latency", execute_latency_ms),
    )


# History Tables - Owned by History component


class HistoryTable(Base):
    """
    History facts table - stores normalized, PII-light facts.

    Owned by History component.
    """

    __tablename__ = "history"

    fact_id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    fact_text = Column(String, nullable=False)
    intent_type = Column(String(64), nullable=False)
    entities = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    outcome = Column(Boolean, nullable=False)
    source_plan_id = Column(String(26), nullable=True)
    fact_hash = Column(String(64), nullable=False)
    ttl_days = Column(Integer, nullable=False, default=30)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "idx_history_user_intent_active",
            user_id,
            intent_type,
            created_at.desc(),
            postgresql_where=deleted_at.is_(None),
        ),
        Index(
            "idx_history_user_fact_hash",
            user_id,
            fact_hash,
            unique=True,
            postgresql_where=deleted_at.is_(None),
        ),
        Index(
            "idx_history_expires_at",
            expires_at,
            postgresql_where=deleted_at.is_(None),
        ),
        Index(
            "idx_history_user_entities",
            entities,
            postgresql_using="gin",
            postgresql_where=deleted_at.is_(None),
        ),
        Index(
            "idx_history_source_plan",
            source_plan_id,
            postgresql_where=source_plan_id.isnot(None),
        ),
    )


class FactPatternTable(Base):
    """
    Detected recurring patterns - derived from history facts.

    Owned by History component.
    """

    __tablename__ = "fact_patterns"

    pattern_id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    intent_type = Column(String(64), nullable=False)
    pattern_key = Column(String(128), nullable=False)
    pattern_description = Column(String(512), nullable=False)
    entity_pattern = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    occurrence_count = Column(Integer, nullable=False, default=1)
    last_seen = Column(DateTime(timezone=True), nullable=False)
    confidence = Column(Float, nullable=False, default=0.0)

    __table_args__ = (
        UniqueConstraint(
            user_id,
            intent_type,
            pattern_key,
            name="uq_fact_patterns_user_intent_key",
        ),
        Index(
            "idx_fact_patterns_user_intent",
            user_id,
            intent_type,
            confidence.desc(),
        ),
        Index("idx_fact_patterns_last_seen", last_seen),
    )


# PluginRegistry Tables - Owned by PluginRegistry component


class ToolTable(Base):
    """
    Tools table - registered external integrations.

    Owned by PluginRegistry component.
    Stores credential vault IDs only, NEVER actual secrets.
    """

    __tablename__ = "tools"

    tool_id = Column(String(128), primary_key=True)
    display_name = Column(String(255), nullable=False)
    credential_template = Column(String(512), nullable=False)
    mcp_server = Column(String(128), nullable=False)
    transport = Column(String(32), nullable=False, server_default=text("'stdio'"))
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    __table_args__ = (
        Index(
            "idx_tools_active",
            tool_id,
            postgresql_where=text("active = TRUE"),
        ),
    )


class OperationTable(Base):
    """
    Operations table - capabilities of a registered tool.

    Owned by PluginRegistry component.
    """

    __tablename__ = "operations"

    id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    operation_id = Column(String(128), nullable=False)
    tool_id = Column(
        String(128),
        ForeignKey("tools.tool_id", ondelete="CASCADE"),
        nullable=False,
    )
    mcp_tool = Column(String(255), nullable=False)
    previewable = Column(Boolean, nullable=False, default=False)
    idempotent = Column(Boolean, nullable=False, default=False)
    scopes = Column(
        ARRAY(String),
        nullable=False,
        server_default=text("'{}'"),
    )
    compensation = Column(String(128), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "tool_id",
            "operation_id",
            name="uq_operations_tool_operation",
        ),
        Index("idx_operations_tool", tool_id),
    )


class RegistryVersionTable(Base):
    """
    Registry versions table - monotonically increasing version counter.

    Owned by PluginRegistry component.
    """

    __tablename__ = "registry_versions"

    version = Column(Integer, primary_key=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    change_summary = Column(String(512), nullable=False)


# PolicyEngine Tables - Owned by PolicyEngine component


class PolicyTable(Base):
    """
    Policies table - policy rules for spawn/step evaluation.

    Owned by PolicyEngine component. Reference: GLOBAL_SPEC §2.9.
    """

    __tablename__ = "policies"

    policy_id = Column(String(128), primary_key=True)
    name = Column(String(256), nullable=False)
    version = Column(Integer, nullable=False, server_default=text("1"))
    scope = Column(String(32), nullable=False)  # step, role, system
    allowed_tools = Column(JSONB, nullable=False, server_default=text("'[\"*\"]'"))
    allowed_roles = Column(JSONB, nullable=False, server_default=text("'[]'"))
    max_spawned_steps = Column(Integer, nullable=False, server_default=text("3"))
    require_approval = Column(Boolean, nullable=False, server_default=text("false"))
    data_access = Column(JSONB, nullable=False, server_default=text("'[\"tier1\"]'"))
    forbidden_actions = Column(JSONB, nullable=False, server_default=text("'[]'"))
    token_budget = Column(Integer, nullable=False, server_default=text("8192"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_policies_scope", scope),
        Index("idx_policies_version", policy_id, version),
    )


class PolicyAttestationTable(Base):
    """
    Policy attestations table - audit records for spawned steps.

    Owned by PolicyEngine component. Reference: GLOBAL_SPEC §2.4.1.
    """

    __tablename__ = "policy_attestations"

    attestation_id = Column(String(26), primary_key=True)  # ULID format
    plan_id = Column(String(26), ForeignKey("plans.plan_id"), nullable=False)
    plan_revision = Column(Integer, nullable=False)
    spawned_by_step = Column(Integer, nullable=False)
    new_steps = Column(JSONB, nullable=False)
    policy_id = Column(String(128), ForeignKey("policies.policy_id"), nullable=False)
    policy_version = Column(Integer, nullable=False)
    decision = Column(JSONB, nullable=False)
    attested_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_policy_attestations_plan_id", plan_id),
        Index("idx_policy_attestations_policy_id", policy_id),
        Index("idx_policy_attestations_attested_at", attested_at),
    )


# Credential Vault Tables - Owned by PluginRegistry component


class CredentialVaultTable(Base):
    """
    Credential vault table - AES-256-GCM encrypted credentials.

    Owned by PluginRegistry component. LLM never sees plaintext values.
    Credentials are decrypted at execution time by ExecuteOrchestrator only.
    """

    __tablename__ = "credential_vault"

    credential_id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_id = Column(
        String(128),
        ForeignKey("tools.tool_id", ondelete="CASCADE"),
        nullable=False,
    )
    encrypted_value = Column(LargeBinary, nullable=False)
    iv = Column(LargeBinary, nullable=False)
    key_version = Column(Integer, nullable=False, server_default=text("1"))
    credential_metadata = Column("metadata", JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    __table_args__ = (
        Index("idx_credential_vault_user_tool", user_id, tool_id),
        Index("idx_credential_vault_user_id", user_id),
    )


# ExecutionMonitor Tables - Owned by ExecutionMonitor component


class ExecutionTrackerTable(Base):
    """
    Execution tracker table - monitors running plan executions.

    Owned by ExecutionMonitor component.
    Background watchdog polls this table to detect stuck/timed-out executions.
    """

    __tablename__ = "execution_tracker"

    tracker_id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    plan_id = Column(String(26), nullable=False)
    user_id = Column(String(255), nullable=False)
    trace_id = Column(String(255), nullable=False)
    status = Column(String(32), nullable=False, server_default=text("'running'"))
    total_steps = Column(Integer, nullable=False, server_default=text("0"))
    completed_steps = Column(Integer, nullable=False, server_default=text("0"))
    error_type = Column(String(64), nullable=True)
    error_details = Column(JSONB, nullable=True)
    notification_sent = Column(Boolean, nullable=False, server_default=text("false"))
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    last_progress_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "idx_execution_tracker_active",
            status,
            started_at,
            postgresql_where=text("status = 'running'"),
        ),
        Index("idx_execution_tracker_plan_id", plan_id),
        Index("idx_execution_tracker_user_id", user_id),
    )


# Audit Tables - Owned by Audit component


class AuditEventTable(Base):
    """
    Audit events table - immutable, append-only audit log.

    Owned by Audit component. No UPDATE allowed; only INSERT and DELETE (retention).
    """

    __tablename__ = "audit_events"

    event_id = Column(String(26), primary_key=True)  # ULID
    event_type = Column(String(32), nullable=False)
    plan_id = Column(String(26), nullable=True)
    user_id = Column(String(255), nullable=True)
    trace_id = Column(String(255), nullable=True)
    step_number = Column(Integer, nullable=True)
    event_data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_audit_events_plan_id", plan_id, postgresql_where=text("plan_id IS NOT NULL")),
        Index("idx_audit_events_user_id", user_id, postgresql_where=text("user_id IS NOT NULL")),
        Index(
            "idx_audit_events_trace_id", trace_id, postgresql_where=text("trace_id IS NOT NULL")
        ),
        Index("idx_audit_events_event_type", event_type),
        Index("idx_audit_events_created_at", created_at),
        Index(
            "idx_audit_events_plan_created",
            plan_id,
            created_at,
            postgresql_where=text("plan_id IS NOT NULL"),
        ),
    )
