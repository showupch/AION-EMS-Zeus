"""Prediction accuracy foundation for AION EMS Zeus.

Compares the nearest available forecast row with current measured state. The
engine is recommendation-only and never calls services or controls devices.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any


class PredictionAccuracyEngine:
    """Track real forward forecast-versus-measured comparisons.

    Forecasts are captured before their target time and only scored after the
    target matures. This avoids the false-confidence problem of comparing a
    freshly recalculated forecast with the measurement that already exists.
    """

    LEAD_HOURS = (1, 3, 6)
    METRICS = ("solar", "home", "grid_import", "grid_export", "battery_soc")

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self._samples: deque[dict[str, Any]] = deque(maxlen=168)
        self._pending: deque[dict[str, Any]] = deque(maxlen=96)
        self._queued_markers: deque[str] = deque(maxlen=384)
        self._summary: dict[str, Any] = {
            "status": "Collecting",
            "sample_count": 0,
            "overall_accuracy_percent": None,
            "trust_percent": None,
            "trust_label": "Collecting",
            "metrics": {},
            "metric_detail": {},
            "lead_time_accuracy": {},
            "recent_samples": [],
            "pending_forecasts": 0,
            "mode": "measurement_only",
            "control_permission": False,
        }

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            number = float(value)
            return number if number == number else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _accuracy(predicted: float | None, actual: float | None, metric: str = "") -> float | None:
        if predicted is None or actual is None:
            return None
        floor = 2.0 if metric == "battery_soc" else 150.0
        scale = max(abs(predicted), abs(actual), floor)
        return round(max(0.0, min(100.0, (1.0 - abs(predicted - actual) / scale) * 100.0)), 1)

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def _actual(self) -> dict[str, float | None]:
        flow = self.core.energy_flow.summary() or {}
        flows = flow.get("flows") or flow
        return {
            "solar": self._num(flows.get("solar_power_w")),
            "home": self._num(flows.get("house_power_w") or flows.get("home_power_w")),
            "grid_import": self._num(flows.get("grid_import_power_w")),
            "grid_export": self._num(flows.get("grid_export_power_w")),
            "battery_soc": self._num(flows.get("battery_soc_percent")),
        }

    def _timeline(self) -> list[dict[str, Any]]:
        forecast = self.core.forecast.summary() or {}
        rows = forecast.get("timeline_24h") or forecast.get("hourly") or forecast.get("forecast") or []
        return rows if isinstance(rows, list) else []

    def _capture_forward_forecasts(self, now: datetime) -> None:
        rows = self._timeline()
        if not rows:
            return
        for lead in self.LEAD_HOURS:
            desired = now + timedelta(hours=lead)
            nearest: tuple[float, dict[str, Any], datetime] | None = None
            for row in rows:
                stamp = self._parse_time(row.get("time") or row.get("datetime"))
                if stamp is None or stamp <= now:
                    continue
                distance = abs((stamp - desired).total_seconds())
                if nearest is None or distance < nearest[0]:
                    nearest = (distance, row, stamp)
            if nearest is None or nearest[0] > 45 * 60:
                continue
            _, row, target = nearest
            marker = f"{lead}:{target.isoformat()}"
            if marker in self._queued_markers:
                continue
            predicted = {
                "solar": self._num(row.get("solar_power_w")),
                "home": self._num(row.get("house_power_w")),
                "grid_import": self._num(row.get("grid_import_power_w")),
                "grid_export": self._num(row.get("grid_export_power_w")),
                "battery_soc": self._num(row.get("projected_battery_soc_percent")),
            }
            if not any(value is not None for value in predicted.values()):
                continue
            self._pending.append({
                "created_at": now.isoformat(),
                "target_time": target.isoformat(),
                "lead_hours": lead,
                "predicted": predicted,
            })
            self._queued_markers.append(marker)

    def _mature_forecasts(self, now: datetime, actual: dict[str, float | None]) -> None:
        keep: deque[dict[str, Any]] = deque(maxlen=self._pending.maxlen)
        for pending in self._pending:
            target = self._parse_time(pending.get("target_time"))
            if target is None:
                continue
            if now < target:
                keep.append(pending)
                continue
            age = (now - target).total_seconds()
            # A late refresh is not an aligned measurement. Discard it instead
            # of manufacturing an accuracy score from the wrong point in time.
            if age > 20 * 60:
                continue
            predicted = pending.get("predicted") or {}
            accuracies = {key: self._accuracy(self._num(predicted.get(key)), actual.get(key), key) for key in self.METRICS}
            valid = [value for value in accuracies.values() if value is not None]
            if not valid:
                continue
            errors = {key: (round(actual[key] - self._num(predicted.get(key)), 1) if actual.get(key) is not None and self._num(predicted.get(key)) is not None else None) for key in self.METRICS}
            self._samples.append({
                "timestamp": now.isoformat(),
                "forecast_created_at": pending.get("created_at"),
                "forecast_time": pending.get("target_time"),
                "lead_hours": pending.get("lead_hours"),
                "accuracy_percent": round(sum(valid) / len(valid), 1),
                "metrics": accuracies,
                "errors": errors,
                "predicted": predicted,
                "actual": dict(actual),
            })
        self._pending = keep

    def refresh(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        actual = self._actual()
        self._mature_forecasts(now, actual)
        self._capture_forward_forecasts(now)

        metric_history: dict[str, list[float]] = {key: [] for key in self.METRICS}
        metric_errors: dict[str, list[float]] = {key: [] for key in self.METRICS}
        lead_history: dict[int, list[float]] = {lead: [] for lead in self.LEAD_HOURS}
        for sample in self._samples:
            for key, value in (sample.get("metrics") or {}).items():
                if isinstance(value, (int, float)):
                    metric_history.setdefault(key, []).append(float(value))
            for key, value in (sample.get("errors") or {}).items():
                if isinstance(value, (int, float)):
                    metric_errors.setdefault(key, []).append(float(value))
            lead = int(sample.get("lead_hours") or 0)
            if lead in lead_history and isinstance(sample.get("accuracy_percent"), (int, float)):
                lead_history[lead].append(float(sample["accuracy_percent"]))

        metrics = {key: round(sum(values) / len(values), 1) if values else None for key, values in metric_history.items()}
        metric_detail = {}
        for key in self.METRICS:
            errors = metric_errors.get(key) or []
            metric_detail[key] = {
                "accuracy_percent": metrics.get(key),
                "matched_samples": len(metric_history.get(key) or []),
                "mean_error": round(sum(errors) / len(errors), 1) if errors else None,
                "mean_absolute_error": round(sum(abs(x) for x in errors) / len(errors), 1) if errors else None,
                "unit": "%" if key == "battery_soc" else "W",
            }
        lead_time_accuracy = {
            f"{lead}h": {
                "accuracy_percent": round(sum(values) / len(values), 1) if values else None,
                "sample_count": len(values),
            } for lead, values in lead_history.items()
        }
        sample_scores = [float(s["accuracy_percent"]) for s in self._samples if isinstance(s.get("accuracy_percent"), (int, float))]
        overall = round(sum(sample_scores) / len(sample_scores), 1) if sample_scores else None
        # Trust deliberately needs several *forward* matches before a high label
        # is allowed. Accuracy alone is not treated as evidence strength.
        evidence_factor = min(1.0, len(sample_scores) / 12.0)
        trust = round(overall * (0.65 + 0.35 * evidence_factor), 1) if overall is not None else None
        trust_label = "High" if trust is not None and trust >= 80 and len(sample_scores) >= 12 else "Medium" if trust is not None and trust >= 60 and len(sample_scores) >= 6 else "Low" if trust is not None else "Collecting"

        latest = self._samples[-1] if self._samples else None
        self._summary = {
            "status": "Ready" if self._samples else "Collecting",
            "sample_count": len(self._samples),
            "overall_accuracy_percent": overall,
            "trust_percent": trust,
            "trust_label": trust_label,
            "metrics": metrics,
            "metric_detail": metric_detail,
            "lead_time_accuracy": lead_time_accuracy,
            "latest_comparison": latest,
            "recent_samples": list(self._samples)[-12:],
            "pending_forecasts": len(self._pending),
            "minimum_samples_for_trend": 6,
            "minimum_samples_for_high_trust": 12,
            "forecast_capture_horizons_hours": list(self.LEAD_HOURS),
            "measurement_note": "Trust uses forecasts captured before their target time and scores them only after an aligned measurement matures. Missing or late pairs are discarded, never estimated.",
            "mode": "forward_matched_measurement_only",
            "control_permission": False,
            "updated_at": now.isoformat(),
            "recorder_safe": True,
        }
        return self._summary

    def summary(self) -> dict[str, Any]:
        return self._summary

