"""
Schema Registry Adapter for ProfileStore

Adapter for the universal preference registry.
Provides backward-compatible interface for preference validation.

Reference: LLD.md §6.3
"""

import logging
from typing import Any

from shared.schemas.preference_registry import get_preference_registry

from ..domain.models import UnknownPreferenceError, ValidationError

logger = logging.getLogger(__name__)


class SchemaRegistryAdapter:
    """
    Adapter for universal preference registry.

    Provides backward-compatible interface while using the centralized registry.
    """

    def __init__(self):
        """Initialize schema registry adapter."""
        self.registry = get_preference_registry()
        logger.info(
            f"Schema registry adapter initialized with {len(self.registry.list_preference_keys())} preferences"
        )

    def get_schema(self, preference_key: str) -> dict:
        """
        Get JSON schema for a preference key.

        Args:
            preference_key: Preference key to get schema for

        Returns:
            JSON schema dictionary

        Raises:
            UnknownPreferenceError: If preference key not found in registry
        """
        try:
            definition = self.registry.get_preference_definition(preference_key)
            return definition.get_json_schema()
        except KeyError:
            logger.warning(f"Unknown preference key: {preference_key}")
            raise UnknownPreferenceError(preference_key)

    def validate_value(self, preference_key: str, value: Any) -> bool:
        """
        Validate a value against its schema.

        Args:
            preference_key: Preference key
            value: Value to validate

        Returns:
            True if validation passes

        Raises:
            UnknownPreferenceError: If preference key not found
            ValidationError: If value fails schema validation
        """
        try:
            self.registry.validate_value(preference_key, value)
            return True
        except KeyError:
            logger.warning(f"Unknown preference key: {preference_key}")
            raise UnknownPreferenceError(preference_key)
        except ValueError as e:
            logger.warning(f"Validation failed for {preference_key}: {e!s}")
            raise ValidationError(preference_key=preference_key, value=value, reason=str(e))

    def get_default_value(self, preference_key: str) -> Any:
        """
        Get default value for a preference.

        Args:
            preference_key: Preference key

        Returns:
            Default value from registry

        Raises:
            UnknownPreferenceError: If preference key not found
        """
        try:
            return self.registry.get_default_value(preference_key)
        except KeyError:
            logger.warning(f"Unknown preference key: {preference_key}")
            raise UnknownPreferenceError(preference_key)

    def is_sensitive(self, preference_key: str) -> bool:
        """
        Check if a preference is marked as sensitive.

        Args:
            preference_key: Preference key

        Returns:
            True if preference is marked as sensitive

        Raises:
            UnknownPreferenceError: If preference key not found
        """
        try:
            return self.registry.is_sensitive(preference_key)
        except KeyError:
            logger.warning(f"Unknown preference key: {preference_key}")
            raise UnknownPreferenceError(preference_key)

    def list_preference_keys(self) -> list[str]:
        """
        Get list of all available preference keys.

        Returns:
            List of preference keys in registry
        """
        return self.registry.list_preference_keys()

    def get_preference_info(self, preference_key: str) -> dict:
        """
        Get complete preference information.

        Args:
            preference_key: Preference key

        Returns:
            Dictionary with preference info

        Raises:
            UnknownPreferenceError: If preference key not found
        """
        try:
            definition = self.registry.get_preference_definition(preference_key)
            return {
                "key": preference_key,
                "type": definition.value_type,
                "default": definition.default,
                "sensitive": definition.sensitive,
                "description": definition.description,
                "examples": definition.examples,
                "category": definition.category,
                "validation": definition.validation,
            }
        except KeyError:
            logger.warning(f"Unknown preference key: {preference_key}")
            raise UnknownPreferenceError(preference_key)


# Singleton instance
_schema_registry = None


def get_schema_registry() -> SchemaRegistryAdapter:
    """
    Get singleton schema registry instance.

    Returns:
        SchemaRegistryAdapter: Shared instance
    """
    global _schema_registry
    if _schema_registry is None:
        _schema_registry = SchemaRegistryAdapter()
    return _schema_registry
