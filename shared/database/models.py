"""
Shared Database Models

SQLAlchemy models for core entities used across components.
These models represent the actual database tables.
"""

from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, UUID as SQLAlchemy_UUID, 
    Index, text, ForeignKey
)
from sqlalchemy.dialects.postgresql import JSONB
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


# Add more shared models here as needed
# class HistoryTable(Base):
#     """History/interaction storage - owned by History component."""
#     pass

# class PlanTable(Base):
#     """Plan storage - owned by PlanLibrary component."""
#     pass