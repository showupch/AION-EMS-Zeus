"""Seasonal-memory foundation backed by the existing learning engine."""

from __future__ import annotations

from typing import Any


class SeasonalMemory:
    def build(self, context: dict[str, Any]) -> dict[str, Any]:
        learning = context.get("learning", {})
        return {
            "status": "Learning",
            "history_days": learning.get("history_days", 0) or 0,
            "confidence": learning.get("confidence", 0) or 0,
            "periods": ["today", "week", "month", "year"],
            "message": "Seasonal comparisons will activate as measured history grows.",
        }
