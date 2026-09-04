"""AION EMS Energy Flow Snapshot."""

from __future__ import annotations

from typing import Any
from datetime import datetime, timezone


class EnergyFlowEngine:
    """Builds a read-only energy flow snapshot from Energy Mapping."""

    def __init__(self, event_bus, energy_mapping, registry=None) -> None:
        self.event_bus = event_bus
        self.energy_mapping = energy_mapping
        self.registry = registry
        self.last: dict[str, Any] = {
            "status": "Not generated",
            "summary": "Energy flow has not been generated yet.",
        }

    def _value(self, mapped: dict[str, Any], field: str) -> float | None:
        item = mapped.get(field)
        if not item:
            return None
        value = item.get("value")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _entity(self, mapped: dict[str, Any], field: str) -> str | None:
        item = mapped.get(field)
        return item.get("entity_id") if item else None

    def _power(self, value: float | None) -> dict[str, Any]:
        return {
            "w": value,
            "kw": round(value / 1000, 3) if value is not None else None,
        }


    def _state_power_w(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.energy_mapping.hass.states.get(entity_id)
        if state is None or str(state.state).strip().lower() in {"unknown", "unavailable", "none", ""}:
            return None
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None
        unit = str(state.attributes.get("unit_of_measurement") or "W").strip().lower()
        if unit == "kw":
            value *= 1000.0
        elif unit == "mw":
            value *= 1000000.0
        return max(0.0, value)

    def _registry_device_values(self) -> tuple[list[dict[str, Any]], dict[str, float]]:
        devices_out: list[dict[str, Any]] = []
        totals: dict[str, float] = {}
        devices = (self.registry.data.get("devices", []) if self.registry else [])
        for device in devices:
            if not device.get("enabled", True):
                continue
            entity_id = device.get("power_entity")
            device_type = str(device.get("type") or "custom")
            value = self._state_power_w(entity_id)

            # v14.8.10.2 Martin Heat Pump accounting: explicit split electrical
            # measurements define one registered load: Heating + DHW + configured
            # Cooling electrical input. Thermal production is never counted here.
            if device_type == "heat_pump" and bool(device.get("separate_heating_dhw_measurements", False)):
                heating_entity = str(device.get("heating_electrical_power_entity") or "").strip()
                dhw_entity = str(device.get("dhw_electrical_power_entity") or "").strip()
                cooling_entity = (str(device.get("cooling_electrical_power_entity") or "").strip()
                                  if bool(device.get("cooling_measurements_enabled", False)) else "")
                circuit_entities = [heating_entity, dhw_entity] + ([cooling_entity] if cooling_entity else [])
                if heating_entity and dhw_entity:
                    circuit_values = [self._state_power_w(item) for item in circuit_entities]
                    if all(item is not None for item in circuit_values):
                        value = sum(float(item) for item in circuit_values)
            temperature_entity = str(device.get("temperature_entity") or "").strip() or None
            temperature_c = None
            temperature_unit = None
            if temperature_entity:
                temp_state = self.energy_mapping.hass.states.get(temperature_entity)
                if temp_state and str(temp_state.state).strip().lower() not in {"unknown", "unavailable", "none", ""}:
                    try:
                        temp_value = float(temp_state.state)
                        temperature_unit = str(temp_state.attributes.get("unit_of_measurement") or "°C").strip()
                        if temperature_unit in {"°F", "F"}:
                            temp_value = (temp_value - 32.0) * 5.0 / 9.0
                        elif temperature_unit == "K":
                            temp_value -= 273.15
                        temperature_c = round(temp_value, 1)
                    except (TypeError, ValueError):
                        temperature_c = None
            if value is not None:
                totals[device_type] = totals.get(device_type, 0.0) + value
            devices_out.append({
                "id": device.get("id"), "name": device.get("name"), "type": device_type,
                "power_entity": entity_id, "power_w": value, "available": value is not None,
                "energy_entity": device.get("energy_entity"), "energy_type": device.get("energy_type", "auto"),
                "temperature_entity": temperature_entity, "temperature_c": temperature_c,
                "temperature_available": temperature_c is not None, "temperature_source_unit": temperature_unit,
                # Frontend consumers must know when a registered inverter is
                # HYBRID so they never re-apply the mapped AC daily-energy value
                # over the canonical True-PV period value.
                "hybrid_inverter": bool(device.get("hybrid_inverter")),
                "solar_power_entity": device.get("solar_power_entity"),
            })
        registered_power_entities = {
            str(row.get("power_entity") or "").strip()
            for row in devices
            if str(row.get("power_entity") or "").strip()
        }
        for hub in (self.registry.data.get("switch_hub", []) if self.registry else []):
            entity_id = str(hub.get("power_entity") or "").strip()
            if not entity_id or entity_id in registered_power_entities:
                continue
            state = self.energy_mapping.hass.states.get(entity_id)
            value = None
            if state and str(state.state).lower() not in {"unknown", "unavailable", "none", ""}:
                try:
                    value = float(state.state)
                    if state.attributes.get("unit_of_measurement") == "kW":
                        value *= 1000.0
                    value = max(0.0, value)
                except (TypeError, ValueError):
                    value = None
            if value is not None:
                totals["switch_hub"] = totals.get("switch_hub", 0.0) + value
            devices_out.append({
                "id": hub.get("id"),
                "name": hub.get("name") or hub.get("switch_entity"),
                "type": "switch_hub",
                "power_entity": entity_id,
                "power_w": value,
                "available": value is not None,
                "energy_entity": None,
                "energy_type": "live_power",
                "switch_entity": hub.get("switch_entity"),
                "switch_hub": True,
                "temperature_entity": None,
                "temperature_c": None,
                "temperature_available": False,
                "temperature_source_unit": None,
                "hybrid_inverter": False,
                "solar_power_entity": None,
            })
        return devices_out, totals

    def refresh(self) -> dict[str, Any]:
        # v14.8.4-rc.7: always rebuild the mapping snapshot from current HA states.
        # EnergyFlow previously reused EnergyMapping.summary() while it was Ready,
        # which could leave Solar/Grid/Battery values stale across later flow refreshes.
        # One refresh now captures all mapped source values for this calculation cycle.
        snapshot_started = datetime.now(timezone.utc)
        mapping = self.energy_mapping.refresh()
        mapped = mapping.get("mapped", {})

        solar = self._value(mapped, "solar_power")
        wind = self._value(mapped, "wind_power")
        generator = self._value(mapped, "generator_power")
        house = self._value(mapped, "house_power")
        grid_import = self._value(mapped, "grid_import_power")
        grid_export = self._value(mapped, "grid_export_power")
        grid_power = self._value(mapped, "grid_power")
        grid_options = mapping.get("mapping_options", {})
        grid_mode = grid_options.get("grid_mode", "bidirectional" if grid_power is not None else "separate")
        grid_sign = grid_options.get("grid_power_sign", "positive_import")
        # v15.0.6: explicit separate import/export mappings are authoritative when
        # present. Both channels are positive magnitudes in Home Assistant, so they
        # must not be hidden behind an older saved bidirectional mode when users map
        # both styles at the same time. This keeps Overview/Welcome and Diagnostics
        # on the same canonical flow evidence.
        if grid_import is not None or grid_export is not None:
            grid_mode = "separate"
        if grid_mode == "bidirectional" and grid_power is not None:
            grid_import = None
            grid_export = None
            normalized_grid = grid_power if grid_sign == "positive_import" else -grid_power
            if grid_import is None:
                grid_import = max(normalized_grid, 0.0)
            if grid_export is None:
                grid_export = max(-normalized_grid, 0.0)
        registered_devices, device_totals = self._registry_device_values()
        battery_options = mapping.get("mapping_options", {})
        battery_mode = battery_options.get("battery_mode", "separate" if (self._value(mapped, "battery_charge_power") is not None or self._value(mapped, "battery_discharge_power") is not None) else "bidirectional")
        # Preserve legacy behavior only for installations that already have a
        # bidirectional mapping but no saved sign option. New/edited mappings can
        # explicitly choose unsigned_magnitude, which must never be interpreted
        # as charge or discharge direction.
        battery_sign_configured = "battery_power_sign" in battery_options
        battery_sign = battery_options.get("battery_power_sign", "positive_discharge")
        mapped_battery_power = self._value(mapped, "battery_power")
        mapped_battery_charge = self._value(mapped, "battery_charge_power")
        mapped_battery_discharge = self._value(mapped, "battery_discharge_power")
        # v15.0.6: when dedicated charge/discharge channels are mapped, use those
        # positive-magnitude channels as the canonical battery direction evidence.
        # This also covers installations that still have a combined battery sensor
        # mapped from an earlier configuration.
        if mapped_battery_charge is not None or mapped_battery_discharge is not None:
            battery_mode = "separate"
        battery_dc_current = self._value(mapped, "battery_dc_current")
        battery_dc_voltage = self._value(mapped, "battery_dc_voltage")
        devices_cfg = (self.registry.data.get("devices", []) if self.registry else [])
        hybrid_devices = [d for d in devices_cfg if isinstance(d, dict) and bool(d.get("hybrid_inverter"))]
        hybrid_enabled = bool(hybrid_devices)
        # v14.0.0-alpha.22.6.1.13: canonical generation comes from Inputs / Energy
        # Sources, never from an inverter record. The manually mapped solar_power
        # entity is the site-wide authoritative PV source. Inverter power remains
        # diagnostic telemetry only.
        dedicated_pv_entity = self._entity(mapped, "solar_power")
        dedicated_true_pv = solar
        dc_power_w = None
        if hybrid_enabled and battery_dc_current is not None and battery_dc_voltage is not None:
            # BYD convention confirmed by the installation: positive current = charge,
            # negative current = discharge. Voltage x current gives signed DC power.
            dc_power_w = float(battery_dc_voltage) * float(battery_dc_current)
            mapped_battery_charge = max(dc_power_w, 0.0)
            mapped_battery_discharge = max(-dc_power_w, 0.0)
            battery_mode = "byd_dc_hybrid"

        if battery_mode in {"separate", "byd_dc_hybrid"}:
            battery_charge = max(mapped_battery_charge or 0.0, 0.0) if mapped_battery_charge is not None else 0.0
            battery_discharge = max(mapped_battery_discharge or 0.0, 0.0) if mapped_battery_discharge is not None else 0.0
            battery_power = battery_discharge - battery_charge if (mapped_battery_charge is not None or mapped_battery_discharge is not None) else device_totals.get("battery")
        else:
            raw_battery_power = mapped_battery_power if mapped_battery_power is not None else device_totals.get("battery")
            if battery_sign == "unsigned_magnitude":
                # Magnitude-only sensors contain no directional evidence while the
                # battery is moving power. At a genuine zero/idle reading, however,
                # direction is irrelevant: both charge and discharge are safely 0 W.
                # Publishing that idle state lets House Power use the normal energy
                # balance fallback instead of becoming unavailable just because an
                # unsigned bidirectional battery sensor is mapped.
                if raw_battery_power is not None and abs(float(raw_battery_power)) <= 1.0:
                    battery_power = 0.0
                    battery_charge = 0.0
                    battery_discharge = 0.0
                else:
                    battery_power = None
                    battery_charge = None
                    battery_discharge = None
            else:
                normalized_battery = raw_battery_power if battery_sign == "positive_discharge" else (-raw_battery_power if raw_battery_power is not None else None)
                battery_charge = abs(normalized_battery) if normalized_battery is not None and normalized_battery < 0 else 0.0
                battery_discharge = normalized_battery if normalized_battery is not None and normalized_battery > 0 else 0.0
                battery_power = normalized_battery
        battery_soc = self._value(mapped, "battery_soc")

        # A hybrid inverter's AC output can contain both PV and battery energy.
        # When a dedicated Solar Power entity is configured, it is authoritative.
        # The legacy subtraction fallback remains only for older hybrid mappings.
        solar_raw_ac = solar
        solar_hybrid_correction = 0.0
        solar_power_source = "inputs_solar_power"
        if dedicated_true_pv is not None:
            # Never add battery charge/discharge to this value. The configured
            # PV sensor already represents the desired live solar source.
            solar = dedicated_true_pv
            solar_hybrid_correction = max(0.0, float(solar_raw_ac or 0.0) - float(solar))
            solar_power_source = "inputs_solar_power"
        elif hybrid_enabled and solar is not None and (battery_discharge or 0.0) > 0:
            solar_hybrid_correction = min(float(solar), float(battery_discharge))
            solar = max(0.0, float(solar) - solar_hybrid_correction)
            solar_power_source = "legacy_hybrid_ac_minus_battery_discharge"

        ev_power = device_totals.get("ev_charger", self._value(mapped, "ev_power"))
        heat_pump_power = device_totals.get("heat_pump", self._value(mapped, "heat_pump_power"))
        water_heater_power = device_totals.get("water_heater", self._value(mapped, "water_heater_power"))

        flexible_known_load = sum(v for v in [ev_power, heat_pump_power, water_heater_power] if v is not None)

        house_source = "measured" if house is not None else "unavailable"
        battery_direction_ambiguous = (
            battery_mode == "bidirectional"
            and battery_sign == "unsigned_magnitude"
            and raw_battery_power is not None
            and abs(float(raw_battery_power)) > 1.0
        )
        if house is None and solar is not None and (grid_import is not None or grid_export is not None):
            if battery_direction_ambiguous:
                # Without battery direction, the energy-balance equation has two
                # valid answers. Do not guess and do not publish a false House Power.
                house = None
                house_source = "ambiguous_battery_direction"
            else:
                house = solar + (grid_import or 0.0) + (battery_discharge or 0.0) - (grid_export or 0.0) - (battery_charge or 0.0)
                house = max(house, 0.0)
                house_source = "calculated_energy_balance"

        generation_sources = {
            "solar": self._power(solar),
            "wind": self._power(wind),
            "generator": self._power(generator),
        }
        generation_total = sum(v for v in (solar, wind, generator) if v is not None) if any(v is not None for v in (solar, wind, generator)) else None

        # v14.0.0-alpha.22.8.4: multi-source accounting is additive and
        # source-preserving. Solar remains its own canonical metric; Wind and
        # Generator only contribute when explicitly configured and available.
        source_energy_today = {
            "solar": self._value(mapped, "solar_energy_today"),
            "wind": self._value(mapped, "wind_energy_today"),
            "generator": self._value(mapped, "generator_energy_today"),
        }
        source_energy_total = {
            "solar": self._value(mapped, "solar_energy_total"),
            "wind": self._value(mapped, "wind_energy_total"),
            "generator": self._value(mapped, "generator_energy_total"),
        }
        generation_energy_today = sum(v for v in source_energy_today.values() if v is not None) if any(v is not None for v in source_energy_today.values()) else None
        generation_energy_total = sum(v for v in source_energy_total.values() if v is not None) if any(v is not None for v in source_energy_total.values()) else None
        source_mix_today = {}
        if generation_energy_today and generation_energy_today > 0:
            source_mix_today = {
                key: round((value or 0.0) / generation_energy_today * 100.0, 2)
                for key, value in source_energy_today.items()
                if value is not None
            }

        source_snapshot = {}
        for field in ("solar_power", "grid_power", "grid_import_power", "grid_export_power",
                      "battery_power", "battery_charge_power", "battery_discharge_power", "house_power"):
            item = mapped.get(field) or {}
            if item.get("entity_id"):
                source_snapshot[field] = {
                    "entity_id": item.get("entity_id"),
                    "value": item.get("value"),
                    "last_changed": item.get("last_changed"),
                    "last_updated": item.get("last_updated"),
                }
        source_times = []
        for item in source_snapshot.values():
            raw = item.get("last_updated") or item.get("last_changed")
            if raw:
                try:
                    source_times.append(datetime.fromisoformat(raw))
                except (TypeError, ValueError):
                    pass
        newest_source = max(source_times) if source_times else None
        oldest_source = min(source_times) if source_times else None
        source_skew_ms = round((newest_source - oldest_source).total_seconds() * 1000, 1) if newest_source and oldest_source else None
        snapshot_completed = datetime.now(timezone.utc)

        flows = {
            "solar_power": self._power(solar),
            "wind_power": self._power(wind),
            "generator_power": self._power(generator),
            "generation_power": self._power(generation_total),
            "generation_sources": generation_sources,
            "generation_energy_today_kwh": generation_energy_today,
            "generation_energy_total_kwh": generation_energy_total,
            "generation_energy_sources_today_kwh": source_energy_today,
            "generation_energy_sources_total_kwh": source_energy_total,
            "generation_source_mix_today_percent": source_mix_today,
            "house_power": self._power(house),
            "grid_import_power": self._power(grid_import),
            "grid_export_power": self._power(grid_export),
            "grid_power": self._power(grid_power),
            "battery_power": self._power(battery_power),
            "battery_charge_power": self._power(battery_charge),
            "battery_discharge_power": self._power(battery_discharge),
            "battery_soc_percent": battery_soc,
            "ev_power": self._power(ev_power),
            "heat_pump_power": self._power(heat_pump_power),
            "water_heater_power": self._power(water_heater_power),
            "known_major_loads_power": self._power(flexible_known_load),
            "device_type_totals": {k: self._power(v) for k, v in device_totals.items()},
            "registered_devices": registered_devices,
            "house_power_source": house_source,
            "grid_mode": grid_mode,
            "grid_sign_convention": grid_sign,
            "grid_direction": "importing" if (grid_import or 0) > 0 else "exporting" if (grid_export or 0) > 0 else "idle",
            "battery_mode": battery_mode,
            "battery_sign_convention": battery_sign,
            "battery_sign_explicitly_configured": battery_sign_configured,
            "battery_direction": "ambiguous" if battery_direction_ambiguous else "discharging" if (battery_discharge or 0.0) > 0 else "charging" if (battery_charge or 0.0) > 0 else "idle",
            "battery_direction_ambiguous": battery_direction_ambiguous,
            "battery_accounting_status": (
                "ambiguous_direction" if battery_direction_ambiguous
                else "idle_unsigned_safe" if battery_mode == "bidirectional" and battery_sign == "unsigned_magnitude" and raw_battery_power is not None
                else "direction_known"
            ),
            "battery_power_magnitude_w": abs(raw_battery_power) if battery_mode == "bidirectional" and raw_battery_power is not None else None,
            "battery_dc_current_a": battery_dc_current,
            "battery_dc_voltage_v": battery_dc_voltage,
            "battery_dc_power_w": dc_power_w,
            "battery_power_source": "byd_dc_voltage_x_current" if dc_power_w is not None else "mapped_power",
            "hybrid_inverter_correction_active": hybrid_enabled,
            "solar_power_raw_ac_w": solar_raw_ac,
            "solar_hybrid_correction_w": solar_hybrid_correction,
            "solar_power_corrected_w": solar,
            "solar_power_source": solar_power_source,
            "source_model_version": "atomic_source_snapshot_v2",
            "source_snapshot": source_snapshot,
            "snapshot_started": snapshot_started.isoformat(),
            "snapshot_completed": snapshot_completed.isoformat(),
            "source_skew_ms": source_skew_ms,
            "newest_source_updated": newest_source.isoformat() if newest_source else None,
            "oldest_source_updated": oldest_source.isoformat() if oldest_source else None,
            "hybrid_solar_power_entity": dedicated_pv_entity,
            "hybrid_solar_power_entity_available": dedicated_true_pv is not None,
            "dedicated_pv_entity": dedicated_pv_entity,
            "dedicated_pv_entity_available": dedicated_true_pv is not None,
        }

        available = {
            "solar": solar is not None,
            "house": house is not None,
            "grid_import": grid_import is not None,
            "grid_export": grid_export is not None,
            "battery_power": battery_power is not None,
            "battery_soc": battery_soc is not None,
            "ev": ev_power is not None,
            "heat_pump": heat_pump_power is not None,
            "water_heater": water_heater_power is not None,
        }

        ready_count = len([v for v in available.values() if v])
        quality_score = int((ready_count / len(available)) * 100)

        summary_bits = []
        if solar is not None:
            summary_bits.append(f"Solar {round(solar/1000, 2)} kW")
        if house is not None:
            summary_bits.append(f"House {round(house/1000, 2)} kW")
        if grid_import is not None:
            summary_bits.append(f"Grid import {round(grid_import/1000, 2)} kW")
        if grid_export is not None:
            summary_bits.append(f"Grid export {round(grid_export/1000, 2)} kW")
        if battery_soc is not None:
            summary_bits.append(f"Battery {round(battery_soc, 1)}%")

        self.last = {
            "status": "Ready",
            "quality_score": quality_score,
            "available": available,
            "ready_count": ready_count,
            "field_count": len(available),
            "flows": flows,
            "entities": {
                field: self._entity(mapped, field)
                for field in [
                    "solar_power",
                    "wind_power",
                    "generator_power",
                    "house_power",
                    "grid_import_power",
                    "grid_export_power",
                    "grid_power",
                    "battery_power",
                    "battery_charge_power",
                    "battery_discharge_power",
                    "battery_soc",
                    "ev_power",
                    "heat_pump_power",
                    "water_heater_power",
                ]
            },
            "mapping_options": mapping.get("mapping_options", {}),
            "source_catalog": mapping.get("source_catalog", {}),
            "optional_generation_available": {"wind": wind is not None, "generator": generator is not None},
            "registered_device_count": len(registered_devices),
            "device_type_totals": {k: self._power(v) for k, v in device_totals.items()},
            "registered_devices": registered_devices,
            "summary": " | ".join(summary_bits) if summary_bits else "No mapped live energy values yet.",
            "safety": "Read-only energy flow snapshot. No device control.",
        }

        self.event_bus.publish("EnergyFlowUpdated", "EnergyFlowEngine", {
            "quality_score": quality_score,
            "ready_count": ready_count,
        })
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
