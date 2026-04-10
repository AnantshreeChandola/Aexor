"""Shared fixtures for TrustFilter tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from components.TrustFilter.adapters.regex_scanner import (
    RegexScanner,
)
from components.TrustFilter.domain.models import S2Result
from components.TrustFilter.domain.tree_walker import (
    JsonTreeWalker,
)
from components.TrustFilter.service.filter_service import (
    FilterService,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def regex_scanner() -> RegexScanner:
    """Default S1 regex scanner."""
    return RegexScanner()


@pytest.fixture()
def tree_walker() -> JsonTreeWalker:
    """Default JSON tree walker."""
    return JsonTreeWalker()


@pytest.fixture()
def mock_haiku_clean() -> AsyncMock:
    """Mock S2 adapter that always returns clean."""
    mock = AsyncMock()
    mock.classify.return_value = S2Result(
        verdict="clean",
        confidence=0.95,
        reason="No injection detected",
        degraded=False,
    )
    return mock


@pytest.fixture()
def mock_haiku_injection() -> AsyncMock:
    """Mock S2 adapter that always returns injection."""
    mock = AsyncMock()
    mock.classify.return_value = S2Result(
        verdict="injection",
        confidence=0.94,
        reason="Prompt injection detected",
        degraded=False,
    )
    return mock


@pytest.fixture()
def mock_haiku_unreachable() -> AsyncMock:
    """Mock S2 adapter that always raises."""
    from components.TrustFilter.domain.errors import (
        HaikuUnreachableError,
    )

    mock = AsyncMock()
    mock.classify.side_effect = HaikuUnreachableError(
        "timeout"
    )
    return mock


@pytest.fixture()
def filter_service_clean(
    regex_scanner: RegexScanner,
    mock_haiku_clean: AsyncMock,
    tree_walker: JsonTreeWalker,
) -> FilterService:
    """FilterService with clean S2 mock."""
    return FilterService(
        regex_scanner=regex_scanner,
        haiku_adapter=mock_haiku_clean,
        tree_walker=tree_walker,
    )


@pytest.fixture()
def filter_service_injection(
    regex_scanner: RegexScanner,
    mock_haiku_injection: AsyncMock,
    tree_walker: JsonTreeWalker,
) -> FilterService:
    """FilterService with injection S2 mock."""
    return FilterService(
        regex_scanner=regex_scanner,
        haiku_adapter=mock_haiku_injection,
        tree_walker=tree_walker,
    )


@pytest.fixture()
def filter_service_degraded(
    regex_scanner: RegexScanner,
    mock_haiku_unreachable: AsyncMock,
    tree_walker: JsonTreeWalker,
) -> FilterService:
    """FilterService with unreachable S2 mock."""
    return FilterService(
        regex_scanner=regex_scanner,
        haiku_adapter=mock_haiku_unreachable,
        tree_walker=tree_walker,
    )


def _scan_kwargs(
    plan_id: str = "plan_test_00000000000000001",
    step_number: int = 1,
    trace_id: str = "trace_001",
) -> dict:
    """Default keyword args for FilterService.scan()."""
    return {
        "plan_id": plan_id,
        "step_number": step_number,
        "trace_id": trace_id,
    }


@pytest.fixture()
def scan_kwargs() -> dict:
    return _scan_kwargs()


@pytest.fixture()
def injection_patterns_50() -> list[dict]:
    """Load the 50 injection pattern fixtures."""
    path = FIXTURES_DIR / "injection_patterns_50.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def benign_responses_20() -> list[dict]:
    """Load the 20 benign tool response fixtures."""
    path = FIXTURES_DIR / "benign_tool_responses_20.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)
