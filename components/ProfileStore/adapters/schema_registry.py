"""
Schema Registry Adapter for ProfileStore

File-based preference schema registry with validation.
Loads schemas from schemas/ directory and provides validation.

Reference: LLD.md §6.3
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict
from jsonschema import validate, ValidationError as JSONSchemaValidationError
import os

from ..domain.models import UnknownPreferenceError, ValidationError

logger = logging.getLogger(__name__)


class SchemaRegistryAdapter:
    """
    File-based schema registry for preference validation.
    
    Loads JSON schemas from schemas/ directory and provides validation services.
    Caches schemas in memory for performance.
    """
    
    def __init__(self, schemas_directory: str | Path = None):
        """
        Initialize schema registry.
        
        Args:
            schemas_directory: Path to directory containing JSON schema files
                              If None, uses components/ProfileStore/schemas/
        """
        if schemas_directory is None:
            # Default to schemas/ directory relative to this file
            current_dir = Path(__file__).parent.parent
            schemas_directory = current_dir / "schemas"
        
        self.schemas_directory = Path(schemas_directory)
        self._schema_cache: Dict[str, dict] = {}
        self._load_schemas()
        
        logger.info(f"Schema registry initialized with {len(self._schema_cache)} schemas")

    def _load_schemas(self):
        """Load all JSON schema files from schemas directory."""
        if not self.schemas_directory.exists():
            logger.warning(f"Schemas directory does not exist: {self.schemas_directory}")
            return
        
        for schema_file in self.schemas_directory.glob("*.json"):
            try:
                with open(schema_file, "r") as f:
                    schema = json.load(f)
                
                # Extract preference key from filename (remove .json extension)
                preference_key = schema_file.stem
                
                # Validate the schema itself has required fields
                if "type" not in schema:
                    logger.warning(f"Schema {preference_key} missing 'type' field")
                    continue
                
                self._schema_cache[preference_key] = schema
                logger.debug(f"Loaded schema for preference: {preference_key}")
                
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load schema {schema_file}: {e}")
                continue

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
        schema = self._schema_cache.get(preference_key)
        if schema is None:
            logger.warning(f"Unknown preference key: {preference_key}")
            raise UnknownPreferenceError(preference_key)
        
        return schema

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
        schema = self.get_schema(preference_key)
        
        try:
            validate(instance=value, schema=schema)
            return True
            
        except JSONSchemaValidationError as e:
            logger.warning(
                f"Validation failed for {preference_key}: {e.message}"
            )
            raise ValidationError(
                preference_key=preference_key,
                value=value,
                reason=e.message
            )

    def get_default_value(self, preference_key: str) -> Any:
        """
        Get default value for a preference from schema.
        
        Args:
            preference_key: Preference key
            
        Returns:
            Default value from schema, or None if no default specified
            
        Raises:
            UnknownPreferenceError: If preference key not found
        """
        schema = self.get_schema(preference_key)
        return schema.get("default")

    def is_sensitive(self, preference_key: str) -> bool:
        """
        Check if a preference is marked as sensitive in schema.
        
        Args:
            preference_key: Preference key
            
        Returns:
            True if preference is marked as sensitive
            
        Raises:
            UnknownPreferenceError: If preference key not found
        """
        schema = self.get_schema(preference_key)
        return schema.get("sensitive", False)

    def list_preference_keys(self) -> list[str]:
        """
        Get list of all available preference keys.
        
        Returns:
            List of preference keys in registry
        """
        return list(self._schema_cache.keys())

    def get_preference_info(self, preference_key: str) -> dict:
        """
        Get complete preference information from schema.
        
        Args:
            preference_key: Preference key
            
        Returns:
            Dictionary with schema info (type, default, sensitive, description, etc.)
            
        Raises:
            UnknownPreferenceError: If preference key not found
        """
        schema = self.get_schema(preference_key)
        
        return {
            "key": preference_key,
            "type": schema.get("type"),
            "default": schema.get("default"),
            "sensitive": schema.get("sensitive", False),
            "description": schema.get("description"),
            "title": schema.get("title"),
            "examples": schema.get("examples", []),
            "required": True,  # All preferences are required to have a value
        }

    def reload_schemas(self):
        """
        Reload schemas from filesystem.
        
        Useful for development when schema files change.
        """
        self._schema_cache.clear()
        self._load_schemas()
        logger.info(f"Reloaded {len(self._schema_cache)} schemas")

    def add_schema(self, preference_key: str, schema: dict):
        """
        Add a schema programmatically (for testing).
        
        Args:
            preference_key: Preference key
            schema: JSON schema dictionary
        """
        # Validate the schema has required fields
        if "type" not in schema:
            raise ValueError("Schema must have 'type' field")
        
        self._schema_cache[preference_key] = schema
        logger.debug(f"Added schema for preference: {preference_key}")


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