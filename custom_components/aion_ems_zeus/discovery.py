"""AION EMS entity discovery with smart rules."""

from __future__ import annotations

from typing import Any


class DiscoveryEngine:
    """Read-only smart entity discovery."""

    SYSTEM_CATEGORIES = {
        "solar": ["solar", "pv", "photovoltaic", "inverter"],
        "battery": ["battery", "soc", "state_of_charge", "akku"],
        "grid": ["grid", "import", "export", "meter", "netz"],
        "ev": ["ev", "charger", "wallbox", "car"],
        "heat_pump": ["heat_pump", "heat pump", "waermepumpe", "wärmepumpe", "heating"],
        "water_heater": ["water_heater", "water heater", "boiler", "warmwasser"],
    }

    APPLIANCE_CATEGORIES = {
        "dishwasher": ["dishwasher", "dish_washer", "spuelmaschine", "spülmaschine", "geschirrspueler", "geschirrspüler"],
        "washing_machine": ["washing_machine", "washer", "wash_machine", "waschmaschine"],
        "dryer": ["dryer", "tumble_dryer", "trockner", "waeschetrockner", "wäschetrockner"],
    }

    POWER_UNITS = {"W", "kW"}
    ENERGY_UNITS = {"Wh", "kWh", "MWh"}

    GENERIC_POWER_TERMS = ["power", "leistung"]
    GENERIC_ENERGY_TERMS = ["energy", "energie"]

    def __init__(self, hass, event_bus) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.last: dict[str, Any] = {
            "status": "Not scanned",
            "candidate_count": 0,
            "summary": "Not scanned.",
        }

    def _blob(self, state) -> str:
        attrs = state.attributes
        return f"{state.entity_id} {attrs.get('friendly_name', '')}".lower().replace(" ", "_")

    def _base_info(self, state, score: int, reason: str) -> dict[str, Any]:
        attrs = state.attributes
        return {
            "entity_id": state.entity_id,
            "name": attrs.get("friendly_name", state.entity_id),
            "unit": attrs.get("unit_of_measurement"),
            "device_class": attrs.get("device_class"),
            "score": score,
            "reason": reason,
        }

    def _system_score(self, blob: str, keywords: list[str], unit: str | None, device_class: str | None) -> tuple[int, str]:
        score = 0
        matches = []
        for keyword in keywords:
            key = keyword.lower().replace(" ", "_")
            if key in blob:
                score += 10
                matches.append(key)

        if not matches:
            return 0, "no system keyword match"

        if unit in self.POWER_UNITS or device_class == "power":
            score += 3
        if unit in self.ENERGY_UNITS or device_class == "energy":
            score += 2
        if device_class == "battery":
            score += 5

        return score, "matched: " + ", ".join(matches)

    def _appliance_score(self, blob: str, keywords: list[str], unit: str | None, device_class: str | None) -> tuple[int, str]:
        matches = []
        for keyword in keywords:
            key = keyword.lower().replace(" ", "_")
            if key in blob:
                matches.append(key)

        # Critical fix:
        # Appliances must match appliance keywords explicitly.
        # Generic "power" must NOT create appliance candidates.
        if not matches:
            return 0, "no explicit appliance keyword match"

        score = 30 + (10 * len(matches))
        if unit in self.POWER_UNITS or device_class == "power":
            score += 10
        if unit in self.ENERGY_UNITS or device_class == "energy":
            score += 8

        return score, "explicit appliance match: " + ", ".join(matches)

    def refresh(self) -> dict[str, Any]:
        categories = {k: [] for k in list(self.SYSTEM_CATEGORIES) + list(self.APPLIANCE_CATEGORIES)}
        power_sensors = []
        energy_sensors = []

        for state in self.hass.states.async_all("sensor"):
            attrs = state.attributes
            unit = attrs.get("unit_of_measurement")
            device_class = attrs.get("device_class")
            blob = self._blob(state)

            if unit in self.POWER_UNITS or device_class == "power":
                power_sensors.append(self._base_info(state, 0, "power sensor"))
            if unit in self.ENERGY_UNITS or device_class == "energy":
                energy_sensors.append(self._base_info(state, 0, "energy sensor"))

            for cat, keywords in self.SYSTEM_CATEGORIES.items():
                score, reason = self._system_score(blob, keywords, unit, device_class)
                if score > 0:
                    categories[cat].append(self._base_info(state, score, reason))

            for cat, keywords in self.APPLIANCE_CATEGORIES.items():
                score, reason = self._appliance_score(blob, keywords, unit, device_class)
                if score > 0:
                    categories[cat].append(self._base_info(state, score, reason))

        for cat in categories:
            categories[cat] = sorted(categories[cat], key=lambda x: x["score"], reverse=True)[:5]

        appliance_quality = {
            cat: "Ready" if categories[cat] else "No explicit match"
            for cat in self.APPLIANCE_CATEGORIES
        }

        result = {
            "status": "Ready",
            "candidate_count": sum(len(v) for v in categories.values()),
            "summary": (
                f"Found {sum(len(v) for v in categories.values())} smart category candidates, "
                f"{len(power_sensors)} power sensors, {len(energy_sensors)} energy sensors."
            ),
            "power_sensor_count": len(power_sensors),
            "energy_sensor_count": len(energy_sensors),
            "power_sensors_sample": power_sensors[:10],
            "energy_sensors_sample": energy_sensors[:10],
            "appliance_quality": appliance_quality,
            "rules": {
                "system": "keyword + power/energy hints",
                "appliances": "explicit appliance keyword required",
            },
            "safety": "Read-only smart discovery.",
        }

        for cat, items in categories.items():
            result[f"{cat}_candidates"] = items

        self.last = result
        self.event_bus.publish("SmartDiscoveryUpdated", "DiscoveryEngine", {
            "candidate_count": result["candidate_count"],
            "appliance_quality": appliance_quality,
        })
        return result

    def summary(self) -> dict[str, Any]:
        return self.last
