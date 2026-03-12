"""
Integration tests for PluginRegistry database adapter.

Require a running PostgreSQL instance.
Mark with @pytest.mark.integration for CI isolation.

Reference: LLD.md Section 8.5 item 4
"""

from __future__ import annotations

import pytest

# Integration tests are skipped by default.
# Run with: pytest -m integration --override-ini="addopts="
pytestmark = pytest.mark.skip(
    reason="Integration tests require a running PostgreSQL instance"
)
