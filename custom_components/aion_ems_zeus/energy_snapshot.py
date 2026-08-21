"""Shared authoritative energy snapshot service for AION EMS Zeus."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.util import dt as dt_util


class EnergySnapshotService:
    """Provide one normalized current-day snapshot to intelligence consumers.

    Mapped daily-energy states are the authoritative current-day source used by
    the Energy Overview frontend. Analytics values are fallback-only, keeping
    intelligence narratives synchronized with the main UI.
    """

    FIELD_MAPPINGS = {
        "solar_energy_kwh": "solar_energy_today",
        "house_energy_kwh": "house_energy_today",
        "grid_import_energy_kwh": "grid_import_energy_today",
        "grid_export_energy_kwh": "grid_export_energy_today",
    }

    def __init__(self, core) -> None:
        self.core = core
        self._last: dict[str, Any] = {}

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            number = float(value)
            return max(0.0, number) if number == number else None
        except (TypeError, ValueError):
            return None

    def _mapped_value(self, mapping_key: str) -> tuple[float | None, str | None]:
        mapping = getattr(self.core, "energy_mapping", None)
        mappings = getattr(mapping, "mappings", {}) if mapping is not None else {}
        entity_id = mappings.get(mapping_key) if isinstance(mappings, dict) else None
        state = self.core.hass.states.get(entity_id) if entity_id else None
        if state is None or str(state.state).strip().lower() in {"", "unknown", "unavailable", "none"}:
            return None, entity_id
        value = self._num(state.state)
        if value is None:
            return None, entity_id
        unit = str(state.attributes.get("unit_of_measurement") or "kWh").strip().lower()
        if unit == "wh":
            value /= 1000.0
        elif unit == "mwh":
            value *= 1000.0
        elif unit not in {"kwh", "kilowatt-hour", "kilowatt-hours"}:
            return None, entity_id
        return value, entity_id

    def refresh(self) -> dict[str, Any]:
        analytics = self.core.analytics.summary() or {}
        periods = analytics.get("periods") if isinstance(analytics.get("periods"), dict) else {}
        today = dict(periods.get("today") or analytics.get("today") or {})
        sources: dict[str, str] = {}
        devices = (getattr(self.core.registry, "data", {}) or {}).get("devices", [])
        hybrid_devices = [d for d in devices if isinstance(d, dict) and bool(d.get("hybrid_inverter"))]
        hybrid_enabled = bool(hybrid_devices)
        # Canonical Solar Input comes from Energy Sources mapping, independent of
        # inverter type, ordering, AC-output semantics, or Hybrid flags.
        hybrid_true_pv_entity = str((self.core.energy_mapping.mappings or {}).get("solar_power") or "").strip()
        hybrid_true_pv_configured = bool(hybrid_true_pv_entity)

        # Match the Energy Overview exactly: mapped daily-energy sensors are the
        # authoritative current-day source whenever available. Analytics values
        # are retained only as a fallback. This removes the stale/partial
        # historical totals that previously leaked into Today's Story.
        for output_key, mapping_key in self.FIELD_MAPPINGS.items():
            # A configured HYBRID true-PV sensor owns solar accounting. Never
            # replace that canonical value with the inverter's daily AC-energy
            # meter, because the latter can include battery discharge.
            if output_key == "solar_energy_kwh" and hybrid_true_pv_configured:
                current = self._num(today.get(output_key))
                if current is not None:
                    today[output_key] = current
                    sources[output_key] = str(today.get(f"{output_key}_source") or hybrid_true_pv_entity)
                continue
            mapped, entity_id = self._mapped_value(mapping_key)
            if mapped is not None:
                today[output_key] = mapped
                sources[output_key] = entity_id or "mapped_daily_energy"
                continue
            current = self._num(today.get(output_key))
            if current is not None:
                today[output_key] = current
                sources[output_key] = str(today.get(f"{output_key}_source") or "analytics_period_today")

        # Fronius/hybrid correction is opt-in per registered inverter.
        # When enabled on at least one inverter, the mapped combined inverter AC
        # energy may include battery discharge. Subtract the authoritative daily
        # battery discharge once from the site solar total. Normal inverters and
        # non-hybrid installations are unchanged.
        if hybrid_true_pv_configured:
            # Strict mode: analytics/data-lake true-PV integration is already the
            # canonical solar value. No AC-minus-battery repair is allowed here.
            today["hybrid_true_pv_configured"] = True
            today["solar_energy_kwh_method"] = "inputs_solar_power_integration"
            today["solar_energy_kwh_source"] = hybrid_true_pv_entity
            sources["solar_energy_kwh"] = hybrid_true_pv_entity
        elif hybrid_enabled:
            # Compatibility only for older HYBRID setups with no dedicated PV
            # sensor. This path must disappear once True PV is configured.
            discharge = self._num(today.get("battery_discharge_energy_kwh")) or 0.0
            solar = self._num(today.get("solar_energy_kwh"))
            if solar is not None and discharge > 0:
                today["solar_energy_raw_ac_kwh"] = solar
                today["solar_energy_kwh"] = max(0.0, solar - discharge)
                today["solar_hybrid_correction_kwh"] = min(solar, discharge)
                sources["solar_energy_kwh"] = f"{sources.get('solar_energy_kwh','mapped_solar')} - battery_discharge"

        finance = self.core.finance.summary() or {}
        battery_support = self._num(finance.get("battery_support_to_home_kwh"))
        if battery_support is None:
            discharge = self._num(today.get("battery_discharge_energy_kwh")) or 0.0
            direct_solar = self._num(today.get("direct_solar_consumption_kwh")) or 0.0
            home = self._num(today.get("house_energy_kwh")) or 0.0
            battery_support = min(discharge, max(0.0, home - direct_solar))

        self._last = {
            "date": dt_util.now().date().isoformat(),
            "day_state": "in_progress",
            "authoritative": bool(today),
            "solar_energy_kwh": self._num(today.get("solar_energy_kwh")),
            "house_energy_kwh": self._num(today.get("house_energy_kwh")),
            "grid_import_energy_kwh": self._num(today.get("grid_import_energy_kwh")),
            "grid_export_energy_kwh": self._num(today.get("grid_export_energy_kwh")),
            "battery_charge_energy_kwh": self._num(today.get("battery_charge_energy_kwh")),
            "battery_discharge_energy_kwh": self._num(today.get("battery_discharge_energy_kwh")),
            "battery_support_to_home_kwh": battery_support,
            "sources": sources,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_source": "dedicated_true_pv_strict" if hybrid_true_pv_configured else "mapped_daily_energy_preferred",
            "hybrid_inverter_correction_active": hybrid_enabled,
            "hybrid_true_pv_configured": hybrid_true_pv_configured,
            "hybrid_true_pv_entity": hybrid_true_pv_entity or None,
            "solar_energy_raw_ac_kwh": self._num(today.get("solar_energy_raw_ac_kwh")),
            "solar_hybrid_correction_kwh": self._num(today.get("solar_hybrid_correction_kwh")),
        }
        return dict(self._last)

    def summary(self) -> dict[str, Any]:
        return self.refresh()


__all__ = ["EnergySnapshotService"]
