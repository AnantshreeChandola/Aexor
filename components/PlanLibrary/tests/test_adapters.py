"""
PlanLibrary Adapter Unit Tests

Tests for DatabaseAdapter.
All tests use mocks (no real database or API calls).

Reference: tasks.md T302
"""

import pytest

VALID_ULID = "01HX1234567890ABCDEFGHJKMN"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
