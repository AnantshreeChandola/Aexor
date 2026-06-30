"""
Preference Registry

Centralized registry for all preference definitions using the universal schema.
Replaces individual JSON schema files with a programmatic approach.
"""

import json
import logging
from pathlib import Path
from typing import Any

from jsonschema import ValidationError as JSONSchemaValidationError
from jsonschema import validate

logger = logging.getLogger(__name__)


class PreferenceDefinition:
    """Single preference definition with metadata."""

    def __init__(
        self,
        key: str,
        value_type: str,
        sensitive: bool = False,
        default: Any = None,
        validation: dict[str, Any] | None = None,
        description: str = "",
        examples: list[Any] | None = None,
        category: str = "user",
    ):
        self.key = key
        self.value_type = value_type
        self.sensitive = sensitive
        self.default = default
        self.validation = validation or {}
        self.description = description
        self.examples = examples or []
        self.category = category

    def to_schema_format(self) -> dict:
        """Convert to universal schema format."""
        return {
            "key": self.key,
            "value": self.default,
            "metadata": {
                "type": self.value_type,
                "sensitive": self.sensitive,
                "default": self.default,
                "validation": self.validation,
                "description": self.description,
                "examples": self.examples,
                "category": self.category,
            },
        }

    def get_json_schema(self) -> dict:
        """Get JSON schema for value validation."""
        schema = {"type": self.value_type}

        # Add validation rules based on type
        if self.value_type == "string":
            if "minLength" in self.validation:
                schema["minLength"] = self.validation["minLength"]
            if "maxLength" in self.validation:
                schema["maxLength"] = self.validation["maxLength"]
            if "pattern" in self.validation:
                schema["pattern"] = self.validation["pattern"]
            if "enum" in self.validation:
                schema["enum"] = self.validation["enum"]

        elif self.value_type in ("integer", "number"):
            if "minimum" in self.validation:
                schema["minimum"] = self.validation["minimum"]
            if "maximum" in self.validation:
                schema["maximum"] = self.validation["maximum"]

        return schema


class PreferenceRegistry:
    """
    Central registry for all preference definitions.

    Uses universal schema approach instead of individual files.
    """

    def __init__(self):
        self.preferences: dict[str, PreferenceDefinition] = {}
        self._load_universal_schema()
        self._register_core_preferences()

        logger.info(f"Preference registry initialized with {len(self.preferences)} preferences")

    def _load_universal_schema(self):
        """Load the universal preference schema for validation."""
        schema_path = Path(__file__).parent / "preference_schema.json"
        with schema_path.open() as f:
            self.universal_schema = json.load(f)

    def _register_core_preferences(self):
        """Register core preference definitions."""

        # User preferences
        self.register(
            PreferenceDefinition(
                key="meeting_duration_min",
                value_type="integer",
                default=30,
                validation={"minimum": 15, "maximum": 240},
                description="Default meeting duration in minutes",
                examples=[30, 60, 90],
                category="user",
            )
        )

        self.register(
            PreferenceDefinition(
                key="work_hours",
                value_type="string",
                default="09:00-17:00",
                validation={
                    "pattern": r"^([0-1][0-9]|2[0-3]):[0-5][0-9]-([0-1][0-9]|2[0-3]):[0-5][0-9]$"
                },
                description="Work hours in HH:MM-HH:MM format",
                examples=["09:00-17:00", "10:00-18:00", "08:30-16:30"],
                category="user",
            )
        )

        # Sensitive preferences
        self.register(
            PreferenceDefinition(
                key="passport_number",
                value_type="string",
                sensitive=True,
                validation={"minLength": 6, "maxLength": 20, "pattern": r"^[A-Z0-9]+$"},
                description="Passport number (sensitive data)",
                category="security",
            )
        )

        self.register(
            PreferenceDefinition(
                key="emergency_contact",
                value_type="object",
                sensitive=True,
                default={"name": "", "phone": "", "relationship": ""},
                description="Emergency contact information",
                category="security",
            )
        )

        # System preferences
        self.register(
            PreferenceDefinition(
                key="timezone",
                value_type="string",
                default="America/Chicago",
                validation={"pattern": r"^[A-Za-z]+/[A-Za-z_]+$"},
                description="User timezone (IANA format)",
                examples=["America/New_York", "Europe/London", "Asia/Tokyo"],
                category="system",
            )
        )

        # Notification preferences
        self.register(
            PreferenceDefinition(
                key="notification_settings",
                value_type="object",
                default={"email": True, "sms": False, "push": True, "quiet_hours": "22:00-08:00"},
                description="Notification preferences",
                category="notification",
            )
        )

        # UI preferences
        self.register(
            PreferenceDefinition(
                key="theme",
                value_type="string",
                default="auto",
                validation={"enum": ["light", "dark", "auto"]},
                description="UI theme preference",
                category="ui",
            )
        )

        self.register(
            PreferenceDefinition(
                key="language",
                value_type="string",
                default="en",
                validation={"pattern": r"^[a-z]{2}$"},
                description="Preferred language (ISO 639-1)",
                examples=["en", "es", "fr", "de"],
                category="ui",
            )
        )

    def register(self, preference: PreferenceDefinition):
        """Register a new preference definition."""
        # Validate against universal schema
        schema_format = preference.to_schema_format()
        try:
            validate(instance=schema_format, schema=self.universal_schema)
        except JSONSchemaValidationError as e:
            raise ValueError(f"Invalid preference definition for {preference.key}: {e.message}")

        self.preferences[preference.key] = preference
        logger.debug(f"Registered preference: {preference.key}")

    def get_preference_definition(self, key: str) -> PreferenceDefinition:
        """Get preference definition by key. Returns dynamic definition for unregistered keys."""
        if key in self.preferences:
            return self.preferences[key]
        # Dynamic definition for user-defined preferences
        return PreferenceDefinition(
            key=key,
            value_type="object",  # JSONB accepts any JSON type
            sensitive=False,
            default=None,
            description=f"User-defined preference: {key}",
            category="user",
        )

    def get_default_value(self, key: str) -> Any:
        """Get default value for a preference."""
        if key not in self.preferences:
            return None
        return self.preferences[key].default

    def is_sensitive(self, key: str) -> bool:
        """Check if preference is sensitive."""
        if key not in self.preferences:
            return False
        return self.preferences[key].sensitive

    def validate_value(self, key: str, value: Any) -> bool:
        """Validate a value against preference schema."""
        if key not in self.preferences:
            return True  # User-defined keys accept any JSON value
        preference = self.preferences[key]
        schema = preference.get_json_schema()

        try:
            validate(instance=value, schema=schema)
            return True
        except JSONSchemaValidationError as e:
            raise ValueError(f"Validation failed for {key}: {e.message}")

    def list_preference_keys(self) -> list[str]:
        """Get list of all registered preference keys."""
        return list(self.preferences.keys())

    def get_preferences_by_category(self, category: str) -> list[PreferenceDefinition]:
        """Get all preferences in a category."""
        return [pref for pref in self.preferences.values() if pref.category == category]

    def get_all_definitions(self) -> dict[str, dict]:
        """Get all preferences in schema format."""
        return {key: pref.to_schema_format() for key, pref in self.preferences.items()}


# Singleton instance
_preference_registry = None


def get_preference_registry() -> PreferenceRegistry:
    """Get singleton preference registry instance."""
    global _preference_registry
    if _preference_registry is None:
        _preference_registry = PreferenceRegistry()
    return _preference_registry


# Convenience functions
def register_preference(preference: PreferenceDefinition):
    """Register a new preference (convenience function)."""
    registry = get_preference_registry()
    registry.register(preference)


def get_preference_schema(key: str) -> dict:
    """Get JSON schema for a preference key."""
    registry = get_preference_registry()
    return registry.get_preference_definition(key).get_json_schema()
