"""
Analytics Service for PlanLibrary

Calculates success rates and performance trends for plan patterns.

Reference: LLD.md, tasks.md T202
"""

import logging

from ..adapters.db import DatabaseAdapter
from ..domain.models import PerformanceTrends

logger = logging.getLogger(__name__)


class AnalyticsService:
    """
    Plan pattern analytics service.

    Provides success rates and performance trend analysis.
    """

    def __init__(self, db_adapter: DatabaseAdapter) -> None:
        """
        Initialize analytics service.

        Args:
            db_adapter: Database operations
        """
        self.db = db_adapter
        logger.info(
            "Analytics service initialized",
            extra={"component": "PlanLibrary"},
        )

    async def calculate_success_rates(
        self,
        timeframe_days: int = 30,
    ) -> dict[str, float]:
        """
        Calculate intent-based success rates.

        Groups plans by intent_type and calculates success rate
        for the specified timeframe.

        Args:
            timeframe_days: Number of days to analyze

        Returns:
            Dict mapping intent_type -> success_rate (0.0 to 1.0)
        """
        rates = await self.db.get_success_rates(timeframe_days=timeframe_days)

        logger.info(
            "Success rates calculated",
            extra={
                "intent_count": len(rates),
                "timeframe_days": timeframe_days,
                "component": "PlanLibrary",
                "operation": "calculate_success_rates",
            },
        )

        return rates

    async def get_performance_trends(
        self,
        intent_type: str | None = None,
    ) -> PerformanceTrends:
        """
        Analyze execution performance trends.

        Args:
            intent_type: Optional filter by intent type

        Returns:
            PerformanceTrends with aggregated metrics
        """
        rates = await self.db.get_success_rates(timeframe_days=30)

        if intent_type and intent_type in rates:
            success_rate = rates[intent_type]
        elif rates:
            success_rate = sum(rates.values()) / len(rates)
        else:
            success_rate = 0.0

        trends = PerformanceTrends(
            intent_type=intent_type,
            success_rate=success_rate,
            total_plans=len(rates),
            trend_period_days=30,
        )

        logger.info(
            "Performance trends analyzed",
            extra={
                "intent_type": intent_type,
                "success_rate": success_rate,
                "component": "PlanLibrary",
                "operation": "get_performance_trends",
            },
        )

        return trends
