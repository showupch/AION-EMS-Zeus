"""Analytics Engine public API."""

from ..analytics import HistoricalAnalyticsEngine


class AnalyticsEngine(HistoricalAnalyticsEngine):
    """Stable Zeus 7 name for historical analytics."""


__all__ = ["AnalyticsEngine"]
