"""Behavior and routine learning for Knowledge Engine 2.0."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any


class BehaviorLearning:
    """Derive compact weekday, demand and export patterns from daily history."""

    @staticmethod
    def _num(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def build(self, core: Any) -> dict[str, Any]:
        raw = getattr(getattr(core, "data_lake", None), "data", {}).get("daily_summaries", {})
        buckets: dict[int, list[dict[str, float]]] = defaultdict(list)
        rows: list[tuple[datetime, dict[str, float]]] = []
        for date_key, source in sorted(raw.items()):
            try:
                dt = datetime.fromisoformat(str(date_key))
            except (TypeError, ValueError):
                continue
            row = {
                "solar": self._num(source.get("solar_energy_kwh")),
                "home": self._num(source.get("house_energy_kwh")),
                "import": self._num(source.get("grid_import_energy_kwh")),
                "export": self._num(source.get("grid_export_energy_kwh")),
            }
            rows.append((dt, row)); buckets[dt.weekday()].append(row)

        names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        profiles = []
        for weekday, values in sorted(buckets.items()):
            count = len(values)
            avg = lambda key: round(sum(v[key] for v in values) / count, 2) if count else None
            profiles.append({"weekday": names[weekday], "sample_days": count,
                             "average_solar_kwh": avg("solar"), "average_home_kwh": avg("home"),
                             "average_import_kwh": avg("import"), "average_export_kwh": avg("export")})
        latest = rows[-1][1] if rows else None
        return {
            "status": "Ready" if len(rows) >= 7 else "Learning",
            "history_days": len(rows),
            "weekday_profiles": profiles[:7],
            "latest_day": latest,
            "message": "Weekday behavior profiles are active." if len(rows) >= 7 else "At least seven measured days are required for weekday behavior profiles.",
        }
