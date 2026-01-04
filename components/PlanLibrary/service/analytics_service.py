"""
Analytics Service - Plan success rate analysis and performance trends.

Calculates success rates by intent type, identifies high-performing patterns,
and provides performance trend analysis for system optimization.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PerformanceTrend:
    """Performance trend data for a specific metric."""
    metric_name: str
    timeframe_days: int
    current_value: float
    previous_value: float
    change_percent: float
    trend_direction: str  # "improving", "declining", "stable"


@dataclass
class SuccessAnalytics:
    """Success rate analytics for intent types."""
    intent_type: str
    success_rate: float
    total_executions: int
    successful_executions: int
    avg_execution_time_ms: float
    timeframe_days: int
    confidence_level: str  # "high", "medium", "low"


class PerformanceTrends:
    """Container for multiple performance trends."""
    
    def __init__(self):
        self.trends: List[PerformanceTrend] = []
        self.generated_at: datetime = datetime.now(timezone.utc)
    
    def add_trend(self, trend: PerformanceTrend):
        """Add a performance trend to the collection."""
        self.trends.append(trend)
    
    def get_trend_by_metric(self, metric_name: str) -> Optional[PerformanceTrend]:
        """Get trend for specific metric."""
        return next((t for t in self.trends if t.metric_name == metric_name), None)


class AnalyticsService:
    """
    Plan analytics service for success rate analysis and performance trends.
    
    Provides insights for system optimization by analyzing:
    - Success rates by intent type
    - Performance trends over time
    - High-performing plan patterns
    - Execution time analytics
    """
    
    def __init__(self, db_adapter):
        """
        Initialize analytics service.
        
        Args:
            db_adapter: Database adapter for query execution
        """
        self.db_adapter = db_adapter
        logger.info("AnalyticsService initialized")

    async def calculate_success_rates(
        self, 
        timeframe_days: int = 30
    ) -> Dict[str, SuccessAnalytics]:
        """
        Calculate success rates by intent type.
        
        Analyzes plan execution outcomes over the specified timeframe
        and calculates success rates with confidence levels.
        
        Args:
            timeframe_days: Analysis timeframe in days
            
        Returns:
            Dictionary mapping intent types to SuccessAnalytics
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # Query success rate data from database
            raw_data = await self.db_adapter.get_success_rate_data(timeframe_days)
            
            analytics = {}
            for data in raw_data:
                intent_type = data["intent_type"]
                total_executions = data["total_executions"]
                successful_executions = data["successful_executions"]
                avg_execution_time_ms = data["avg_execution_time_ms"]
                
                # Calculate success rate
                success_rate = (
                    successful_executions / total_executions 
                    if total_executions > 0 
                    else 0.0
                )
                
                # Determine confidence level based on sample size
                confidence_level = self._calculate_confidence_level(total_executions)
                
                analytics[intent_type] = SuccessAnalytics(
                    intent_type=intent_type,
                    success_rate=success_rate,
                    total_executions=total_executions,
                    successful_executions=successful_executions,
                    avg_execution_time_ms=avg_execution_time_ms,
                    timeframe_days=timeframe_days,
                    confidence_level=confidence_level
                )
            
            # Log analytics performance
            latency_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            logger.info(
                "Success rate analytics calculated",
                extra={
                    "timeframe_days": timeframe_days,
                    "intent_types_analyzed": len(analytics),
                    "analytics_latency_ms": latency_ms,
                    "component": "PlanLibrary"
                }
            )
            
            return analytics
            
        except Exception as e:
            logger.error(f"Error calculating success rates: {e}")
            raise

    async def get_performance_trends(
        self,
        intent_type: Optional[str] = None,
        trend_days: int = 30,
        comparison_days: int = 30
    ) -> PerformanceTrends:
        """
        Analyze execution performance trends.
        
        Compares current period performance with previous period
        to identify trends in execution times and success rates.
        
        Args:
            intent_type: Specific intent to analyze (None for all)
            trend_days: Current period duration in days
            comparison_days: Previous period duration for comparison
            
        Returns:
            PerformanceTrends object with trend analysis
        """
        start_time = datetime.now(timezone.utc)
        trends = PerformanceTrends()
        
        try:
            # Define time periods
            now = datetime.now(timezone.utc)
            current_start = now - timedelta(days=trend_days)
            previous_start = current_start - timedelta(days=comparison_days)
            
            # Get performance data for both periods
            current_metrics = await self.db_adapter.get_performance_metrics(
                start_date=current_start,
                end_date=now,
                intent_type=intent_type
            )
            
            previous_metrics = await self.db_adapter.get_performance_metrics(
                start_date=previous_start,
                end_date=current_start,
                intent_type=intent_type
            )
            
            # Calculate trends for different metrics
            trends.add_trend(self._calculate_trend(
                metric_name="avg_execution_time_ms",
                current_value=current_metrics.get("avg_execution_time_ms", 0.0),
                previous_value=previous_metrics.get("avg_execution_time_ms", 0.0),
                timeframe_days=trend_days,
                lower_is_better=True  # Lower execution time is better
            ))
            
            trends.add_trend(self._calculate_trend(
                metric_name="success_rate",
                current_value=current_metrics.get("success_rate", 0.0),
                previous_value=previous_metrics.get("success_rate", 0.0),
                timeframe_days=trend_days,
                lower_is_better=False  # Higher success rate is better
            ))
            
            trends.add_trend(self._calculate_trend(
                metric_name="total_executions",
                current_value=current_metrics.get("total_executions", 0),
                previous_value=previous_metrics.get("total_executions", 0),
                timeframe_days=trend_days,
                lower_is_better=False  # More executions = more data
            ))
            
            # Log trend analysis performance
            latency_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            logger.info(
                "Performance trends calculated",
                extra={
                    "intent_type": intent_type or "all",
                    "trend_days": trend_days,
                    "trends_count": len(trends.trends),
                    "trends_latency_ms": latency_ms,
                    "component": "PlanLibrary"
                }
            )
            
            return trends
            
        except Exception as e:
            logger.error(f"Error calculating performance trends: {e}")
            raise

    async def identify_high_performing_patterns(
        self,
        min_executions: int = 5,
        min_success_rate: float = 0.8,
        timeframe_days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Identify high-performing plan patterns for optimization.
        
        Finds plan patterns that consistently succeed and execute quickly,
        suitable for template generation and optimization.
        
        Args:
            min_executions: Minimum executions for statistical significance
            min_success_rate: Minimum success rate threshold
            timeframe_days: Analysis timeframe
            
        Returns:
            List of high-performing pattern dictionaries
        """
        try:
            patterns = await self.db_adapter.get_high_performing_patterns(
                min_executions=min_executions,
                min_success_rate=min_success_rate,
                timeframe_days=timeframe_days
            )
            
            # Enrich patterns with performance scoring
            enriched_patterns = []
            for pattern in patterns:
                # Calculate performance score (0-100)
                performance_score = self._calculate_performance_score(
                    success_rate=pattern["success_rate"],
                    avg_execution_time=pattern["avg_execution_time_ms"],
                    total_executions=pattern["total_executions"]
                )
                
                pattern["performance_score"] = performance_score
                enriched_patterns.append(pattern)
            
            # Sort by performance score descending
            enriched_patterns.sort(key=lambda x: x["performance_score"], reverse=True)
            
            logger.info(
                "High-performing patterns identified",
                extra={
                    "patterns_found": len(enriched_patterns),
                    "min_success_rate": min_success_rate,
                    "timeframe_days": timeframe_days,
                    "component": "PlanLibrary"
                }
            )
            
            return enriched_patterns
            
        except Exception as e:
            logger.error(f"Error identifying high-performing patterns: {e}")
            raise

    def _calculate_confidence_level(self, total_executions: int) -> str:
        """
        Calculate statistical confidence level based on sample size.
        
        Args:
            total_executions: Number of plan executions
            
        Returns:
            Confidence level: "high", "medium", or "low"
        """
        if total_executions >= 20:
            return "high"
        elif total_executions >= 10:
            return "medium"
        else:
            return "low"

    def _calculate_trend(
        self,
        metric_name: str,
        current_value: float,
        previous_value: float,
        timeframe_days: int,
        lower_is_better: bool = False
    ) -> PerformanceTrend:
        """
        Calculate trend for a specific metric.
        
        Args:
            metric_name: Name of the metric
            current_value: Current period value
            previous_value: Previous period value
            timeframe_days: Timeframe for the trend
            lower_is_better: Whether lower values indicate improvement
            
        Returns:
            PerformanceTrend object
        """
        # Calculate percentage change
        if previous_value > 0:
            change_percent = ((current_value - previous_value) / previous_value) * 100
        else:
            change_percent = 100.0 if current_value > 0 else 0.0
        
        # Determine trend direction
        if abs(change_percent) < 5.0:
            trend_direction = "stable"
        elif change_percent > 0:
            trend_direction = "declining" if lower_is_better else "improving"
        else:
            trend_direction = "improving" if lower_is_better else "declining"
        
        return PerformanceTrend(
            metric_name=metric_name,
            timeframe_days=timeframe_days,
            current_value=current_value,
            previous_value=previous_value,
            change_percent=round(change_percent, 2),
            trend_direction=trend_direction
        )

    def _calculate_performance_score(
        self,
        success_rate: float,
        avg_execution_time: float,
        total_executions: int
    ) -> float:
        """
        Calculate composite performance score (0-100).
        
        Combines success rate, execution speed, and confidence into
        a single score for ranking plan patterns.
        
        Args:
            success_rate: Plan success rate (0.0-1.0)
            avg_execution_time: Average execution time in ms
            total_executions: Total number of executions
            
        Returns:
            Performance score (0.0-100.0)
        """
        # Success rate component (0-40 points)
        success_component = success_rate * 40
        
        # Speed component (0-40 points, inverse relationship)
        # Assume 5000ms is baseline, faster is better
        baseline_time = 5000.0
        speed_ratio = min(1.0, baseline_time / max(avg_execution_time, 100))
        speed_component = speed_ratio * 40
        
        # Confidence component (0-20 points)
        confidence_ratio = min(1.0, total_executions / 20.0)
        confidence_component = confidence_ratio * 20
        
        total_score = success_component + speed_component + confidence_component
        return round(total_score, 1)

    async def get_execution_time_distribution(
        self,
        intent_type: Optional[str] = None,
        timeframe_days: int = 30
    ) -> Dict[str, Any]:
        """
        Analyze execution time distribution for performance insights.
        
        Args:
            intent_type: Specific intent to analyze
            timeframe_days: Analysis timeframe
            
        Returns:
            Dictionary with execution time statistics
        """
        try:
            distribution_data = await self.db_adapter.get_execution_time_distribution(
                intent_type=intent_type,
                timeframe_days=timeframe_days
            )
            
            return {
                "intent_type": intent_type or "all",
                "timeframe_days": timeframe_days,
                "distribution": distribution_data,
                "generated_at": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error analyzing execution time distribution: {e}")
            raise

    async def health_check(self) -> bool:
        """Check analytics service health."""
        try:
            # Test database connectivity
            return await self.db_adapter.health_check()
        except Exception as e:
            logger.error(f"Analytics service health check failed: {e}")
            return False