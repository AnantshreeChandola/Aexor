"""
AnalyticsService Unit Tests

Tests for success rate calculation and performance trend analysis.
Uses mocked adapters.

Reference: tasks.md T204
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from components.PlanLibrary.domain.models import PerformanceTrends
from components.PlanLibrary.service.analytics_service import AnalyticsService


@pytest.fixture
def mock_db_adapter():
    """Create mock database adapter for analytics."""
    adapter = MagicMock()
    adapter.get_success_rates = AsyncMock(return_value={})
    return adapter


@pytest.fixture
def analytics_service(mock_db_adapter):
    """Create AnalyticsService with mocked dependencies."""
    return AnalyticsService(db_adapter=mock_db_adapter)


class TestSuccessRates:
    """Tests for AnalyticsService.calculate_success_rates()."""

    @pytest.mark.asyncio
    async def test_success_rates_calculated(self, analytics_service, mock_db_adapter):
        """Success rates calculated correctly (US-4 scenario 1)."""
        mock_db_adapter.get_success_rates.return_value = {
            "schedule_meeting": 0.85,
            "book_restaurant": 0.72,
            "send_email": 0.95,
        }

        result = await analytics_service.calculate_success_rates(timeframe_days=30)

        assert isinstance(result, dict)
        assert result["schedule_meeting"] == 0.85
        assert result["book_restaurant"] == 0.72
        assert result["send_email"] == 0.95

    @pytest.mark.asyncio
    async def test_success_rates_empty(self, analytics_service, mock_db_adapter):
        """Success rates return empty dict when no data."""
        mock_db_adapter.get_success_rates.return_value = {}

        result = await analytics_service.calculate_success_rates()
        assert result == {}

    @pytest.mark.asyncio
    async def test_success_rates_custom_timeframe(self, analytics_service, mock_db_adapter):
        """Success rates respect custom timeframe."""
        await analytics_service.calculate_success_rates(timeframe_days=7)

        mock_db_adapter.get_success_rates.assert_called_once_with(timeframe_days=7)


class TestPerformanceTrends:
    """Tests for AnalyticsService.get_performance_trends()."""

    @pytest.mark.asyncio
    async def test_performance_trends_aggregated(self, analytics_service, mock_db_adapter):
        """Performance trends aggregated (US-4 scenario 2)."""
        mock_db_adapter.get_success_rates.return_value = {
            "schedule_meeting": 0.85,
            "book_restaurant": 0.72,
        }

        result = await analytics_service.get_performance_trends(intent_type="schedule_meeting")

        assert isinstance(result, PerformanceTrends)
        assert result.intent_type == "schedule_meeting"
        assert result.success_rate == 0.85
        assert result.trend_period_days == 30

    @pytest.mark.asyncio
    async def test_performance_trends_all_intents(self, analytics_service, mock_db_adapter):
        """Performance trends for all intents averages success rates."""
        mock_db_adapter.get_success_rates.return_value = {
            "a": 0.8,
            "b": 0.6,
        }

        result = await analytics_service.get_performance_trends()
        assert result.intent_type is None
        assert result.success_rate == pytest.approx(0.7)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
