"""Finance and fixed-tariff calculations for AION EMS Zeus."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.util import dt as dt_util


class FinanceEngine:
    """Calculate transparent financial values from measured daily energy."""

    def __init__(self, event_bus, registry, analytics, device_analytics, data_quality) -> None:
        self.event_bus = event_bus
        self.registry = registry
        self.analytics = analytics
        self.device_analytics = device_analytics
        self.data_quality = data_quality
        self.last: dict[str, Any] = {"status": "Not configured"}

    @staticmethod
    def _num(value: Any) -> float:
        try:
            value = float(value)
            return value if value >= 0 else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _state_energy_kwh(self, entity_id: str | None) -> float:
        """Read a mapped energy entity and normalize Wh/kWh/MWh to kWh."""
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            return 0.0
        state = self.analytics.hass.states.get(entity_id)
        if state is None or str(state.state).lower() in {"unknown", "unavailable", "none", ""}:
            return 0.0
        try:
            value = max(float(state.state), 0.0)
        except (TypeError, ValueError):
            return 0.0
        unit = str(state.attributes.get("unit_of_measurement") or "").strip().lower()
        if unit == "wh":
            value /= 1000.0
        elif unit == "mwh":
            value *= 1000.0
        elif unit != "kwh":
            return 0.0
        return value

    def _canonical_battery_today(self, today: dict[str, Any], key: str, mapping_field: str) -> tuple[float, str]:
        """Resolve current-day battery energy without allowing a stale layer to zero it."""
        candidates: list[tuple[float, str]] = [(self._num(today.get(key)), "analytics_period")]
        now_key = dt_util.now().date().isoformat()
        raw_daily = ((getattr(self.analytics, "data_lake", None).data or {}).get("daily_summaries", {})
                     if getattr(self.analytics, "data_lake", None) is not None else {})
        raw_today = dict((raw_daily or {}).get(now_key) or {})
        candidates.append((self._num(raw_today.get(key)), "datalake_today"))

        # Battery Statistics reads the current Home Assistant Energy history from
        # Analytics' recorder cache. Finance must consult that exact same cache so
        # the value shown as "Discharged Today" cannot diverge from Battery Support.
        ha_days = getattr(self.analytics, "_ha_energy_days", {}) or {}
        ha_today = ((ha_days.get(key) or {}).get(now_key) if isinstance(ha_days, dict) else None)
        candidates.append((self._num(ha_today), "analytics_ha_energy_today"))

        mappings = dict((getattr(self.registry, "data", {}) or {}).get("entity_mappings", {}) or {})
        mapped = self._state_energy_kwh(mappings.get(mapping_field))
        candidates.append((mapped, f"mapped:{mappings.get(mapping_field)}" if mappings.get(mapping_field) else "mapped_daily"))
        value, source = max(candidates, key=lambda item: item[0])
        return value, source

    def refresh(self) -> dict[str, Any]:
        cfg = self.registry.data.get("sources", {}).get("tariffs", {})
        currency = str(cfg.get("currency") or "CHF").upper()[:4]
        enabled = bool(cfg.get("enabled"))
        import_rate = self._num(cfg.get("import_tariff"))
        export_rate = self._num(cfg.get("export_tariff"))
        standing = self._num(cfg.get("standing_charge"))
        today = self.analytics.summary().get("periods", {}).get("today", {})
        imported = self._num(today.get("grid_import_energy_kwh"))
        exported = self._num(today.get("grid_export_energy_kwh"))
        solar = self._num(today.get("solar_energy_kwh"))
        house = self._num(today.get("house_energy_kwh"))
        battery_charge, battery_charge_source = self._canonical_battery_today(
            today, "battery_charge_energy_kwh", "battery_charge_energy_today"
        )
        battery_discharge, battery_discharge_source = self._canonical_battery_today(
            today, "battery_discharge_energy_kwh", "battery_discharge_energy_today"
        )

        # Energy-value flow: direct solar and battery support are valued separately.
        # Canonical Solar Input represents measured site PV on the generation side.
        # Direct solar therefore excludes measured grid export only. Battery charging
        # is a separate flow and must not be subtracted from PV a second time.
        direct_solar = self._num(today.get("direct_solar_consumption_kwh"))
        # A canonical Solar Input can be changed during the current day. Recorder
        # grid totals still cover the whole calendar day, while the new PV
        # integration begins at the source change. If export is greater than the
        # available solar total, that period is provably incomplete. Reconstruct
        # today's local supply from the measured home/grid/battery boundary until
        # the next clean midnight rollover.
        solar_period_complete = solar + 0.05 >= exported
        # Battery Statistics owns the measured discharged-today total. Allocate
        # measured battery support first, then cap direct solar to the remaining
        # home demand. The previous order let direct solar consume all home demand
        # and could therefore force Battery Support to 0 despite a valid measured
        # discharge value.
        local_home_supply = max(0.0, house - imported)
        battery_to_home = min(battery_discharge, local_home_supply)
        remaining_home_after_battery = max(0.0, local_home_supply - battery_to_home)

        if solar_period_complete:
            solar_available_for_home = max(0.0, solar - exported)
            direct_solar = min(solar_available_for_home, remaining_home_after_battery)
        else:
            # Transition-day fallback: preserve the measured battery allocation and
            # assign only the residual local home supply to solar.
            direct_solar = remaining_home_after_battery

        grid_cost = imported * import_rate
        export_revenue = exported * export_rate
        direct_solar_value = direct_solar * import_rate
        battery_support_value = battery_to_home * import_rate
        avoided_import_value = direct_solar_value + battery_support_value
        net_benefit = avoided_import_value + export_revenue - grid_cost - standing
        devices = []
        for device in self.device_analytics.summary().get("devices", []):
            energy = self._num(device.get("energy_today_kwh"))
            devices.append({
                "id": device.get("id"), "name": device.get("name"),
                "energy_today_kwh": round(energy, 4),
                "estimated_cost": round(energy * import_rate, 4) if enabled else None,
                "energy_source": device.get("method", "unknown"),
            })
        confidence = self.data_quality.summary().get("confidence_score")
        self.last = {
            "status": "Ready" if enabled else "Not configured",
            "configured": enabled, "currency": currency, "tariff_mode": "fixed",
            "vat_included": bool(cfg.get("vat_included", True)),
            "import_tariff": import_rate if enabled else None,
            "export_tariff": export_rate if enabled else None,
            "standing_charge": standing if enabled else None,
            "grid_import_kwh": round(imported, 4), "grid_export_kwh": round(exported, 4),
            "solar_self_consumed_kwh": round(direct_solar, 4),
            "direct_solar_to_home_kwh": round(direct_solar, 4),
            "battery_charge_kwh": round(battery_charge, 4),
            "battery_discharge_kwh": round(battery_discharge, 4),
            "battery_support_to_home_kwh": round(battery_to_home, 4),
            "battery_charge_source": battery_charge_source,
            "battery_discharge_source": battery_discharge_source,
            "grid_cost_today": round(grid_cost, 4) if enabled else None,
            "export_revenue_today": round(export_revenue, 4) if enabled else None,
            "solar_value_today": round(direct_solar_value, 4) if enabled else None,
            "battery_support_value_today": round(battery_support_value, 4) if enabled else None,
            "avoided_import_value_today": round(avoided_import_value, 4) if enabled else None,
            "net_benefit_today": round(net_benefit, 4) if enabled else None,
            "device_costs": devices, "data_confidence": confidence,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "solar_period_complete": solar_period_complete,
            "assumptions": "Fixed tariffs. Direct solar and measured battery discharge to the home are valued as avoided grid purchases. Canonical solar excludes measured export; battery charging is tracked separately. If the Solar Input changes mid-day and the solar period is incomplete, Today is temporarily reconstructed from measured home, grid and battery totals until midnight.",
        }
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last

__all__ = ["FinanceEngine"]
