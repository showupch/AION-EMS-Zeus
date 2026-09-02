"""Data integrity, mapping validation and confidence diagnostics for Zeus."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .device_roles import is_consuming_load

BAD_STATES = {"unknown", "unavailable", "none", ""}
POWER_UNITS = {"W", "kW"}
ENERGY_UNITS = {"Wh", "kWh", "MWh"}
SOC_UNITS = {"%"}


class DataQualityEngine:
    """Continuously validate mappings, devices, freshness and energy balance."""

    STALE_SECONDS = 300
    IDLE_POWER_W = 25.0
    WARN_BALANCE_PERCENT = 5.0
    ERROR_BALANCE_PERCENT = 15.0
    WARN_BALANCE_W = 250.0
    MAX_POWER_W = 100_000.0

    def __init__(self, hass, event_bus, energy_flow, registry=None) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.energy_flow = energy_flow
        self.registry = registry
        self._observations: dict[str, dict[str, Any]] = {}
        self.last: dict[str, Any] = {
            "status": "Waiting",
            "quality_score": 0,
            "confidence_score": 0,
            "issues": [],
        }

    @staticmethod
    def _w(flows: dict[str, Any], key: str) -> float | None:
        item = flows.get(key)
        value = item.get("w") if isinstance(item, dict) else None
        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _severity(score: int) -> str:
        return "error" if score < 50 else "warning"

    def _expected(self, field: str) -> tuple[set[str], set[str]]:
        if field == "battery_soc":
            return SOC_UNITS, {"battery"}
        if "energy" in field:
            return ENERGY_UNITS, {"energy"}
        return POWER_UNITS, {"power"}

    def _observe_interval(self, entity_id: str, stamp: datetime) -> float | None:
        iso = stamp.isoformat()
        item = self._observations.setdefault(entity_id, {"stamp": iso, "intervals": []})
        previous = item.get("stamp")
        if previous and previous != iso:
            try:
                interval = max(0.0, (stamp - datetime.fromisoformat(previous)).total_seconds())
                if interval > 0:
                    intervals = item.setdefault("intervals", [])
                    intervals.append(interval)
                    del intervals[:-12]
            except (TypeError, ValueError):
                pass
            item["stamp"] = iso
        intervals = item.get("intervals", [])
        return round(sum(intervals) / len(intervals), 1) if intervals else None

    def _validate_entity(self, label: str, entity_id: str | None, expected_kind: str, now: datetime) -> dict[str, Any]:
        result: dict[str, Any] = {
            "label": label,
            "entity_id": entity_id,
            "score": 100,
            "status": "Healthy",
            "issues": [],
            "last_update_seconds": None,
            "average_update_interval_seconds": None,
        }
        if not entity_id:
            result.update(score=0, status="Missing", issues=["entity_not_configured"])
            return result
        state = self.hass.states.get(entity_id)
        if state is None:
            result.update(score=0, status="Missing", issues=["entity_missing"])
            return result

        raw = str(state.state).lower()
        unit = state.attributes.get("unit_of_measurement")
        device_class = str(state.attributes.get("device_class") or "").lower()
        state_class = str(state.attributes.get("state_class") or "").lower()
        age = max(0.0, (now - state.last_updated).total_seconds())
        result["last_update_seconds"] = round(age, 1)
        result["average_update_interval_seconds"] = self._observe_interval(entity_id, state.last_updated)
        result["unit"] = unit
        result["device_class"] = device_class or None
        result["state_class"] = state_class or None
        result["friendly_name"] = state.attributes.get("friendly_name") or entity_id
        result["current_state"] = state.state
        result["recommendation"] = "No action required."

        if entity_id.startswith("sensor.aion_ems_zeus_"):
            result["score"] -= 80
            result["issues"].append("circular_aion_mapping")
        if raw in BAD_STATES:
            result["score"] -= 70
            result["issues"].append("unavailable")
            result["status"] = "Unavailable"
            result["recommendation"] = "Check the device or Home Assistant integration."

        numeric_value: float | None = None
        try:
            numeric_value = float(state.state)
        except (TypeError, ValueError):
            pass

        if age > self.STALE_SECONDS and raw not in BAD_STATES:
            # Event-driven device power and cumulative-energy sensors often do not
            # update while a device is off. Classify those as idle/sleeping rather
            # than reporting a false stale alarm.
            if expected_kind == "power" and numeric_value is not None:
                value_w = numeric_value * 1000 if unit == "kW" else numeric_value
                if abs(value_w) <= self.IDLE_POWER_W:
                    result["status"] = "Idle"
                    result["recommendation"] = "No action required; the device is currently idle."
                else:
                    result["score"] -= min(35, 10 + int((age - self.STALE_SECONDS) / 120))
                    result["issues"].append("delayed")
                    result["status"] = "Delayed"
                    result["recommendation"] = "Check whether this sensor updates only when its value changes."
            elif expected_kind == "energy" and numeric_value is not None:
                result["status"] = "Sleeping"
                result["recommendation"] = "No action required if this cumulative sensor updates only when energy changes."
            else:
                result["score"] -= min(35, 10 + int((age - self.STALE_SECONDS) / 120))
                result["issues"].append("delayed")
                result["status"] = "Delayed"
                result["recommendation"] = "Check the source integration if updates do not resume."

        expected_units = POWER_UNITS if expected_kind == "power" else ENERGY_UNITS if expected_kind == "energy" else SOC_UNITS
        if unit not in expected_units:
            result["score"] -= 25
            result["issues"].append("unexpected_unit")
        if expected_kind in {"power", "energy"} and device_class and device_class != expected_kind:
            result["score"] -= 15
            result["issues"].append("unexpected_device_class")
        if expected_kind == "energy" and state_class and state_class not in {"total", "total_increasing", "measurement"}:
            result["score"] -= 10
            result["issues"].append("unexpected_state_class")

        try:
            value = float(state.state)
            if expected_kind == "power":
                value_w = value * 1000 if unit == "kW" else value
                if abs(value_w) > self.MAX_POWER_W:
                    result["score"] -= 35
                    result["issues"].append("implausible_value")
            elif expected_kind == "soc" and not 0 <= value <= 100:
                result["score"] -= 40
                result["issues"].append("soc_out_of_range")
        except (TypeError, ValueError):
            result["score"] -= 50
            result["issues"].append("non_numeric")

        result["score"] = max(0, int(result["score"]))
        if result["status"] not in {"Idle", "Sleeping", "Delayed", "Unavailable"}:
            result["status"] = "Error" if result["score"] < 50 else "Warning" if result["issues"] else "Healthy"
        return result

    def _device_health(self, now: datetime) -> list[dict[str, Any]]:
        devices = self.registry.data.get("devices", []) if self.registry else []
        output: list[dict[str, Any]] = []
        for device in devices[:40]:
            if not device.get("enabled", True):
                continue
            consuming_load = is_consuming_load(device)
            power = self._validate_entity("Power", device.get("power_entity"), "power", now)
            energy = self._validate_entity("Energy", device.get("energy_entity"), "energy", now)

            # Device-health authority:
            # - End-use loads require Power + Energy.
            # - Generation/source/meter devices use cumulative Energy as the
            #   authoritative health channel. Their live Power input is optional
            #   for this DEVICE-health score because some valid source devices
            #   expose only lifetime/period energy in Home Assistant.
            #   If optional Power is healthy we still display it; if not, it is
            #   explicitly marked N/A rather than creating a false 50% warning.
            if consuming_load:
                power["applicable"] = True
                energy["applicable"] = True
            else:
                power["applicable"] = bool(power.get("score", 0) > 0)
                energy["applicable"] = True
                if not power["applicable"]:
                    power["status"] = "Not applicable"

            optional: list[dict[str, Any]] = []
            for key, label in (("state_entity", "State"), ("availability_entity", "Availability")):
                entity_id = device.get(key)
                if entity_id:
                    state = self.hass.states.get(entity_id)
                    optional.append({
                        "label": label,
                        "entity_id": entity_id,
                        "status": "Healthy" if state and str(state.state).lower() not in BAD_STATES else "Warning",
                    })
            scored_inputs = [item for item in (power, energy) if item.get("applicable", True)]
            score = (
                round(sum(item["score"] for item in scored_inputs) / len(scored_inputs))
                if scored_inputs
                else 100
            )
            output.append({
                "id": device.get("id"),
                "name": device.get("name") or device.get("id") or "Device",
                "type": device.get("type", "custom"),
                "score": score,
                "status": "Excellent" if score >= 95 else "Good" if score >= 80 else "Warning" if score >= 50 else "Error",
                "power": power,
                "energy": energy,
                "optional": optional,
            })
        return output

    def _mapping_suggestions(self, device_health: list[dict[str, Any]]) -> list[dict[str, Any]]:
        suggestions: list[dict[str, Any]] = []
        # Only make safe read-only suggestions; never mutate registry mappings.
        all_states = self.hass.states.async_all("sensor")
        for device in device_health:
            if device["energy"]["score"] > 0:
                continue
            name_tokens = {t for t in str(device["name"]).lower().replace("_", " ").split() if len(t) > 2}
            candidates = []
            for state in all_states:
                entity_text = f"{state.entity_id} {state.name}".lower()
                unit = state.attributes.get("unit_of_measurement")
                device_class = state.attributes.get("device_class")
                if unit not in ENERGY_UNITS and device_class != "energy":
                    continue
                matches = sum(1 for token in name_tokens if token in entity_text)
                if matches:
                    candidates.append((matches, state.entity_id))
            if candidates:
                candidates.sort(reverse=True)
                suggestions.append({
                    "device_id": device["id"],
                    "device_name": device["name"],
                    "field": "energy_entity",
                    "suggested_entity": candidates[0][1],
                    "action": "review_only",
                })
        return suggestions[:10]

    def refresh(self) -> dict[str, Any]:
        flow = self.energy_flow.summary()
        flows = flow.get("flows", {})
        entities = flow.get("entities", {})
        now = datetime.now(timezone.utc)
        issues: list[dict[str, Any]] = []
        source_scores: dict[str, dict[str, Any]] = {}

        for field, entity_id in entities.items():
            if not entity_id:
                continue
            expected_kind = "soc" if field == "battery_soc" else "energy" if "energy" in field else "power"
            check = self._validate_entity(field.replace("_", " ").title(), entity_id, expected_kind, now)
            source_scores[field] = check
            for code in check["issues"]:
                issues.append({
                    "severity": self._severity(check["score"]),
                    "code": code,
                    "source": field,
                    "entity_id": entity_id,
                    "friendly_name": check.get("friendly_name"),
                    "status": check.get("status"),
                    "current_state": check.get("current_state"),
                    "last_update_seconds": check.get("last_update_seconds"),
                    "recommendation": check.get("recommendation"),
                })

        solar = self._w(flows, "solar_power")
        house = self._w(flows, "house_power")
        grid_import = self._w(flows, "grid_import_power") or 0.0
        grid_export = self._w(flows, "grid_export_power") or 0.0
        battery_charge = self._w(flows, "battery_charge_power") or 0.0
        battery_discharge = self._w(flows, "battery_discharge_power") or 0.0
        difference_w = None
        difference_percent = None
        balance_score = 100
        if solar is not None and house is not None:
            supply = solar + grid_import + battery_discharge
            demand = house + grid_export + battery_charge
            difference_w = round(supply - demand, 1)
            denominator = max(supply, demand, 1.0)
            difference_percent = round(abs(difference_w) / denominator * 100, 1)
            if difference_percent >= self.ERROR_BALANCE_PERCENT:
                balance_score = 20
                issues.append({"severity": "error", "code": "energy_balance_error", "difference_w": difference_w, "difference_percent": difference_percent})
            elif difference_percent >= self.WARN_BALANCE_PERCENT and abs(difference_w) >= self.WARN_BALANCE_W:
                balance_score = 65
                issues.append({"severity": "warning", "code": "energy_balance_warning", "difference_w": difference_w, "difference_percent": difference_percent})
        else:
            balance_score = 65

        device_health = self._device_health(now)
        device_score = round(sum(d["score"] for d in device_health) / len(device_health)) if device_health else 100
        source_values = [item["score"] for item in source_scores.values()]
        mapping_score = round(sum(source_values) / len(source_values)) if source_values else 70
        overall = round(mapping_score * 0.50 + balance_score * 0.25 + device_score * 0.25)
        error_count = sum(1 for issue in issues if issue["severity"] == "error")
        warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
        status = "Error" if error_count else "Warning" if warning_count else "Healthy"
        stale_count = sum(1 for item in source_scores.values() if "delayed" in item["issues"])
        invalid_mapping_count = sum(1 for item in source_scores.values() if item["score"] < 50)
        suggestions = self._mapping_suggestions(device_health)

        self.last = {
            "status": status,
            "quality_score": overall,
            "confidence_score": overall,
            "confidence_label": "Excellent" if overall >= 95 else "Good" if overall >= 80 else "Limited" if overall >= 60 else "Low",
            "mapping_score": mapping_score,
            "device_score": device_score,
            "source_scores": source_scores,
            "device_health": device_health,
            "mapping_suggestions": suggestions,
            "energy_balance": {
                "difference_w": difference_w,
                "difference_percent": difference_percent,
                "score": balance_score,
                "status": "Healthy" if balance_score == 100 else "Warning" if balance_score >= 50 else "Error",
                "warning_tolerance_percent": self.WARN_BALANCE_PERCENT,
                "error_tolerance_percent": self.ERROR_BALANCE_PERCENT,
            },
            "delayed_sensor_count": stale_count,
            "stale_sensor_count": stale_count,
            "invalid_mapping_count": invalid_mapping_count,
            "error_count": error_count,
            "warning_count": warning_count,
            "issues": issues[:30],
            "summary": f"System confidence {overall}% ({status}); {stale_count} delayed sensor(s), {invalid_mapping_count} invalid mapping(s).",
            "generated_at": now.isoformat(),
            "safety": "Read-only validation and suggestions. No device control or automatic mapping changes.",
        }
        self.event_bus.publish("DataQualityUpdated", "DataQualityEngine", {"quality_score": overall, "status": status})
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
