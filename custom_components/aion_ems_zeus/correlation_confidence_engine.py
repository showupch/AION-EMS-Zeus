"""Cross-system correlation and evidence confidence for AION EMS Zeus."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CorrelationAssessment:
    classification: str
    confidence_percent: int
    supporting_evidence: tuple[str, ...]
    weak_evidence: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    conflicts: tuple[str, ...]
    subsystem_agreement: dict[str, str]
    confidence_components: dict[str, int]
    summary: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "confidence_percent": self.confidence_percent,
            "supporting_evidence": list(self.supporting_evidence),
            "weak_evidence": list(self.weak_evidence),
            "missing_evidence": list(self.missing_evidence),
            "conflicts": list(self.conflicts),
            "subsystem_agreement": self.subsystem_agreement,
            "confidence_components": self.confidence_components,
            "summary": self.summary,
            "recommendation_only": True,
            "recorder_safe": True,
        }


class CorrelationConfidenceEngine:
    """Weight existing subsystem evidence without polling or controlling devices."""

    def __init__(self, event_bus, core) -> None:
        self.event_bus = event_bus
        self.core = core

    @staticmethod
    def _summary(obj: Any) -> dict[str, Any]:
        try:
            data = obj.summary() or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _number(*values: Any) -> float | None:
        for value in values:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number == number:
                return number
        return None

    @staticmethod
    def _status(value: Any) -> str:
        return str(value or "").strip().lower()

    def summary(self) -> dict[str, Any]:
        root = self._summary(self.core.root_cause_intelligence)
        flow = self._summary(self.core.energy_flow)
        forecast = self._summary(self.core.forecast)
        weather = self._summary(self.core.weather)
        quality = self._summary(self.core.data_quality)
        qa = self._summary(self.core.qa_diagnostics)
        runtime = self._summary(self.core.runtime_resilience)
        consistency = self._summary(self.core.data_consistency)

        supporting: list[str] = []
        weak: list[str] = []
        missing: list[str] = []
        conflicts: list[str] = []
        agreement: dict[str, str] = {}

        # EnergyFlowEngine publishes live measurements under summary()["flows"]
        # as {"w": ..., "kw": ...} power objects. Older Correlation code read
        # non-existent top-level *_w keys, which made healthy live energy look
        # completely unavailable. Keep legacy aliases as fallbacks for upgrade
        # compatibility, but prefer the canonical nested flow contract.
        flow_values = flow.get("flows") if isinstance(flow.get("flows"), dict) else {}
        live_values = [
            self._number((flow_values.get("solar_power") or {}).get("w") if isinstance(flow_values.get("solar_power"), dict) else flow_values.get("solar_power"), flow.get("solar_power_w"), flow.get("solar_w"), flow.get("pv_power_w")),
            self._number((flow_values.get("house_power") or {}).get("w") if isinstance(flow_values.get("house_power"), dict) else flow_values.get("house_power"), flow.get("house_power_w"), flow.get("home_power_w"), flow.get("load_power_w")),
            self._number((flow_values.get("grid_import_power") or {}).get("w") if isinstance(flow_values.get("grid_import_power"), dict) else flow_values.get("grid_import_power"), flow.get("grid_import_w"), flow.get("import_power_w")),
            self._number((flow_values.get("grid_export_power") or {}).get("w") if isinstance(flow_values.get("grid_export_power"), dict) else flow_values.get("grid_export_power"), flow.get("grid_export_w"), flow.get("export_power_w")),
        ]
        live_count = sum(value is not None for value in live_values)
        live_score = min(100, 25 * live_count)
        if live_count >= 3:
            supporting.append(f"Live energy flow provides {live_count}/4 core measurements")
            agreement["live_energy"] = "supports"
        elif live_count:
            weak.append(f"Only {live_count}/4 core live measurements are available")
            agreement["live_energy"] = "partial"
        else:
            missing.append("Live energy-flow evidence is unavailable")
            agreement["live_energy"] = "missing"

        forecast_conf = self._number(forecast.get("confidence_percent"), forecast.get("confidence"))
        forecast_method = str(forecast.get("method") or "").strip()
        if forecast_conf is None:
            missing.append("Forecast confidence is unavailable")
            forecast_score = 35
            agreement["forecast"] = "missing"
        else:
            forecast_score = int(max(0, min(100, forecast_conf)))
            label = f"Forecast confidence is {forecast_score}%"
            if forecast_score >= 75:
                supporting.append(label + (f" using {forecast_method}" if forecast_method else ""))
                agreement["forecast"] = "supports"
            else:
                weak.append(label + (f" using {forecast_method}" if forecast_method else ""))
                agreement["forecast"] = "weak"

        weather_condition = self._status(weather.get("condition") or weather.get("state"))
        weather_status = self._status(weather.get("status"))
        if weather_condition:
            supporting.append(f"Weather context reports {weather_condition.replace('-', ' ')}")
            weather_score = 85
            agreement["weather"] = "available"
        elif weather_status and weather_status not in {"unknown", "unavailable"}:
            weak.append(f"Weather source reports {weather_status} without a future condition")
            weather_score = 55
            agreement["weather"] = "partial"
        else:
            missing.append("Timestamped weather evidence is unavailable")
            weather_score = 35
            agreement["weather"] = "missing"

        quality_score = self._number(quality.get("score"), quality.get("quality_score"))
        qa_errors = int(self._number(qa.get("error_count")) or 0)
        qa_warnings = int(self._number(qa.get("warning_count")) or 0)
        if quality_score is None:
            quality_score = 55
            weak.append("Data-quality score is not available")
            agreement["data_quality"] = "partial"
        elif quality_score >= 85 and qa_errors == 0:
            supporting.append(f"Data quality is {quality_score:.0f}% with no QA errors")
            agreement["data_quality"] = "supports"
        else:
            weak.append(f"Data quality is {quality_score:.0f}% with {qa_errors} error(s) and {qa_warnings} warning(s)")
            agreement["data_quality"] = "weak"

        runtime_status = self._status(runtime.get("status"))
        if runtime_status in {"healthy", "excellent", "good", "stable", "ok"}:
            supporting.append(f"Runtime resilience is {runtime_status}")
            runtime_score = 90
            agreement["runtime"] = "supports"
        elif runtime_status:
            weak.append(f"Runtime resilience is {runtime_status}")
            runtime_score = 55
            agreement["runtime"] = "weak"
        else:
            missing.append("Runtime-resilience status is unavailable")
            runtime_score = 45
            agreement["runtime"] = "missing"

        consistency_status = self._status(consistency.get("status"))
        issue_count = int(self._number(consistency.get("issue_count"), consistency.get("issues")) or 0)
        if consistency_status in {"healthy", "consistent", "ok", "good"} or issue_count == 0:
            supporting.append("Cross-sensor consistency reports no material conflict")
            consistency_score = 90
            agreement["consistency"] = "supports"
        else:
            weak.append(f"Cross-sensor consistency reports {issue_count or 'possible'} issue(s)")
            consistency_score = 55
            agreement["consistency"] = "weak"

        solar, home, grid_import, grid_export = [value or 0.0 for value in live_values]
        if grid_import > 250 and grid_export > 250:
            conflicts.append("Grid import and export are both materially active")
        if solar > 0 and home <= 0 and grid_export <= 0:
            conflicts.append("Solar production is present but no destination flow is measured")
        # Do not call low instantaneous solar a weather conflict without a
        # time-aligned expected-power reference. Clear/sunny weather near dawn,
        # dusk or with array geometry can legitimately coincide with low PV.
        # Weather remains supporting context, while measured live energy is the
        # primary evidence source.
        expected_live_solar = self._number(
            forecast.get("expected_current_solar_power_w"),
            forecast.get("expected_solar_power_w"),
            forecast.get("current_expected_power_w"),
        )
        if (
            weather_condition
            and any(x in weather_condition for x in ("sunny", "clear"))
            and expected_live_solar is not None
            and expected_live_solar >= 500
            and solar < expected_live_solar * 0.15
        ):
            conflicts.append("Measured solar is materially below the time-aligned clear-weather expectation")
        if root.get("primary_cause") and not root.get("evidence"):
            conflicts.append("Root-cause conclusion has no published evidence")

        conflict_penalty = min(30, len(conflicts) * 10)
        missing_penalty = min(18, len(missing) * 4)
        components = {
            "live_energy": int(live_score),
            "forecast": int(forecast_score),
            "weather": int(weather_score),
            "data_quality": int(max(0, min(100, quality_score))),
            "runtime": int(runtime_score),
            "consistency": int(consistency_score),
        }
        weighted = (
            components["live_energy"] * 0.27
            + components["forecast"] * 0.18
            + components["weather"] * 0.10
            + components["data_quality"] * 0.20
            + components["runtime"] * 0.10
            + components["consistency"] * 0.15
        )
        confidence = int(max(25, min(98, round(weighted - conflict_penalty - missing_penalty))))

        if conflicts:
            classification = "Conflicting evidence"
        elif missing:
            classification = "Supported with limitations"
        elif confidence >= 90:
            classification = "Strong cross-system agreement"
        elif confidence >= 75:
            classification = "Good cross-system agreement"
        else:
            classification = "Limited cross-system agreement"

        summary = (
            f"{classification}. {len(supporting)} supporting signal(s), "
            f"{len(weak)} weak signal(s), {len(missing)} missing source(s), "
            f"and {len(conflicts)} conflict(s)."
        )
        return CorrelationAssessment(
            classification=classification,
            confidence_percent=confidence,
            supporting_evidence=tuple(supporting[:7]),
            weak_evidence=tuple(weak[:5]),
            missing_evidence=tuple(missing[:5]),
            conflicts=tuple(conflicts[:5]),
            subsystem_agreement=agreement,
            confidence_components=components,
            summary=summary,
        ).as_dict()
