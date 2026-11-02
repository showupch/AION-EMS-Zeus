"""Compact pattern detection for Zeus 12.1 alpha.2."""
from __future__ import annotations
from typing import Any


class PatternDetector:
    def build(self, behavior: dict[str, Any]) -> dict[str, Any]:
        profiles = behavior.get("weekday_profiles", [])
        patterns: list[dict[str, Any]] = []
        valid = [p for p in profiles if p.get("sample_days", 0) > 0]
        if valid:
            high_load = max(valid, key=lambda p: p.get("average_home_kwh") or 0)
            high_solar = max(valid, key=lambda p: p.get("average_solar_kwh") or 0)
            high_export = max(valid, key=lambda p: p.get("average_export_kwh") or 0)
            patterns.extend([
                {"type": "highest_load_weekday", "label": high_load["weekday"], "value_kwh": high_load.get("average_home_kwh")},
                {"type": "strongest_solar_weekday", "label": high_solar["weekday"], "value_kwh": high_solar.get("average_solar_kwh")},
                {"type": "highest_export_weekday", "label": high_export["weekday"], "value_kwh": high_export.get("average_export_kwh")},
            ])
        return {"status": "Ready" if patterns else "Learning", "pattern_count": len(patterns), "patterns": patterns[:6]}
