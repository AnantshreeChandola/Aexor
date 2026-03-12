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
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from .adapter import Base


class UserTable(Base):
    """
    Users table - core identity for all components.

    Owned by Auth/Registration component but referenced by others.
    """
    __tablename__ = "users"

    user_id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()")
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
        SQLAlchemy_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()")
    )
    user_id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False
    )
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
        SQLAlchemy_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()")
    )
    plan_id = Column(
        String(26),
        ForeignKey("plans.plan_id", ondelete="CASCADE"),
        nullable=False
    )
    success = Column(Boolean, nullable=False)
    error_type = Column(String(64), nullable=True)
    error_details = Column(JSONB, nullable=True)
    execution_start = Column(DateTime, nullable=False)
    execution_end = Column(DateTime, nullable=False)
    total_steps = Column(Integer, nullable=False)
    failed_step = Column(Integer, nullable=True)
    context_data = Column(JSONB, nullable=True)

    __table_args__ = (
        Index("idx_plan_outcomes_plan_id", plan_id),
        Index("idx_plan_outcomes_success", success),
        Index("idx_plan_outcomes_execution_start", execution_start),
    )


class PlanEmbeddingTable(Base):
    """
    Plan embeddings table - stores vector embeddings for similarity search.

    Owned by PlanLibrary component. Requires pgvector extension.
    """
    __tablename__ = "plan_embeddings"

    embedding_id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()")
    )
    plan_id = Column(
        String(26),
        ForeignKey("plans.plan_id", ondelete="CASCADE"),
        nullable=False,
        unique=True  # One embedding per plan
    )
    # Note: vector column will be added via pgvector extension
    # vector = Column(Vector(1536), nullable=False)
    model_version = Column(String(32), nullable=False, default="text-embedding-ada-002")
    created_at = Column(DateTime, nullable=False, server_default=text("NOW()"))
    vector_norm = Column(String, nullable=False)  # Store as JSON for now

    __table_args__ = (
        Index("idx_plan_embeddings_plan_id", plan_id),
        Index("idx_plan_embeddings_created_at", created_at),
    )


class PlanMetricsTable(Base):
    """
    Plan metrics table - stores performance metrics.

    Owned by PlanLibrary component.
    """
    __tablename__ = "plan_metrics"

    metrics_id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()")
    )
    plan_id = Column(
        String(26),
        ForeignKey("plans.plan_id", ondelete="CASCADE"),
        nullable=False
    )
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
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "idx_history_user_intent_active",
            user_id, intent_type, created_at.desc(),
            postgresql_where=deleted_at.is_(None),
        ),
        Index(
            "idx_history_user_fact_hash",
            user_id, fact_hash,
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
            user_id, intent_type, pattern_key,
            name="uq_fact_patterns_user_intent_key",
        ),
        Index(
            "idx_fact_patterns_user_intent",
            user_id, intent_type, confidence.desc(),
        ),
        Index("idx_fact_patterns_last_seen", last_seen),
    )


# PluginRegistry Tables - Owned by PluginRegistry component

class ToolTable(Base):
    """
    Tools table - registered external integrations.

    Owned by PluginRegistry component.
    Stores credential ID templates only, NEVER actual secrets.
    """
    __tablename__ = "tools"

    tool_id = Column(String(128), primary_key=True)
    display_name = Column(String(255), nullable=False)
    credential_template = Column(String(512), nullable=False)
    n8n_credential_type = Column(String(128), nullable=False)
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
    n8n_node = Column(String(255), nullable=False)
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
            "tool_id", "operation_id",
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
