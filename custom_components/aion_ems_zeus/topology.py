"""Multi-inverter topology and operating-mode engine for AION EMS Zeus.

Read-only by design. The engine reads Home Assistant states and registry metadata
only. It never calls inverter, battery, device, or automation control services.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


INVERTER_TYPES = {"solar_inverter", "inverter", "pv_inverter", "microinverter"}
INVALID_STATES = {"unknown", "unavailable", "none", ""}
ACTIVE_W = 20.0
PV_ACTIVE_W = 50.0
BATTERY_ACTIVE_W = 50.0


class MultiInverterTopologyEngine:
    """Build individual inverter metrics, operating modes and topology."""

    def __init__(self, hass, event_bus, registry, energy_flow) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.registry = registry
        self.energy_flow = energy_flow
        self.last: dict[str, Any] = {
            "status": "Not generated",
            "inverter_count": 0,
            "summary": "No topology snapshot has been generated yet.",
            "safety": "Read-only topology. Recommendation Only.",
        }

    @staticmethod
    def _number(state) -> float | None:
        if state is None or str(state.state).lower() in INVALID_STATES:
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _power_w(self, entity_id: str | None) -> float | None:
        state = self.hass.states.get(entity_id) if entity_id else None
        value = self._number(state)
        if value is None:
            return None
        unit = str(state.attributes.get("unit_of_measurement") or "W").lower()
        if unit == "kw":
            value *= 1000
        elif unit == "mw":
            value *= 1_000_000
        return round(value, 2)

    def _energy_kwh(self, entity_id: str | None) -> float | None:
        state = self.hass.states.get(entity_id) if entity_id else None
        value = self._number(state)
        if value is None:
            return None
        unit = str(state.attributes.get("unit_of_measurement") or "kWh").lower()
        if unit == "wh":
            value /= 1000
        elif unit == "mwh":
            value *= 1000
        return round(value, 3)

    @staticmethod
    def _is_inverter(device: dict[str, Any]) -> bool:
        dtype = str(device.get("type") or "").lower()
        category = str(device.get("category") or "").lower()
        groups = {str(x).lower() for x in (device.get("group_ids") or [])}
        return dtype in INVERTER_TYPES or (category == "generation" and "solar" in groups)

    def _ha_metadata(self, device: dict[str, Any]) -> dict[str, Any]:
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        source_device_id = device.get("source_device_id")
        if not source_device_id:
            for entity_id in (device.get("power_entity"), device.get("energy_entity")):
                entry = entity_registry.async_get(entity_id) if entity_id else None
                if entry and entry.device_id:
                    source_device_id = entry.device_id
                    break
        ha_device = device_registry.async_get(source_device_id) if source_device_id else None
        identifiers = []
        if ha_device:
            for item in ha_device.identifiers:
                parts = [str(x) for x in item if x is not None]
                if parts:
                    identifiers.append(":".join(parts))
        return {
            "source_device_id": source_device_id,
            "manufacturer": device.get("manufacturer") or (ha_device.manufacturer if ha_device else None),
            "model": device.get("model") or (ha_device.model if ha_device else None),
            "serial_number": device.get("serial_number") or (ha_device.serial_number if ha_device else None),
            "sw_version": device.get("firmware") or (ha_device.sw_version if ha_device else None),
            "identifiers": identifiers[:4],
        }

    def _device_entities(self, source_device_id: str | None) -> list[str]:
        if not source_device_id:
            return []
        registry = er.async_get(self.hass)
        entries = getattr(registry, "entities", {})
        values = entries.values() if hasattr(entries, "values") else []
        return [entry.entity_id for entry in values if getattr(entry, "device_id", None) == source_device_id]

    def _device_signals(self, source_device_id: str | None) -> dict[str, Any]:
        """Find optional DC/status signals from sibling entities on one HA device."""
        result: dict[str, Any] = {
            "dc_voltage_v": None, "dc_current_a": None, "dc_power_w": None,
            "dc_voltage_entity": None, "dc_current_entity": None, "dc_power_entity": None,
            "status_entity": None, "raw_status": None, "reachable": False,
            "battery_capable": False,
        }
        for entity_id in self._device_entities(source_device_id):
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            raw = str(state.state)
            available = raw.lower() not in INVALID_STATES
            result["reachable"] = result["reachable"] or available
            friendly = str(state.attributes.get("friendly_name") or "")
            text = f"{entity_id} {friendly}".lower().replace("-", "_")
            unit = str(state.attributes.get("unit_of_measurement") or "").lower()
            if "battery" in text or "storage" in text:
                result["battery_capable"] = True
            value = self._number(state)
            if value is not None and ("dc" in text or "photovolta" in text or "pv" in text):
                if unit in {"v", "volt", "volts"} and result["dc_voltage_v"] is None:
                    result["dc_voltage_v"] = round(value, 2)
                    result["dc_voltage_entity"] = entity_id
                elif unit in {"a", "amp", "amps"} and result["dc_current_a"] is None:
                    result["dc_current_a"] = round(value, 3)
                    result["dc_current_entity"] = entity_id
                elif unit in {"w", "kw"} and "power" in text and result["dc_power_w"] is None:
                    result["dc_power_w"] = round(value * (1000 if unit == "kw" else 1), 2)
                    result["dc_power_entity"] = entity_id
            if result["status_entity"] is None and any(k in text for k in ("status", "state", "mode")):
                if not any(k in text for k in ("battery_state", "charge_state", "state_of_charge")):
                    result["status_entity"] = entity_id
                    result["raw_status"] = raw
        if result["dc_power_w"] is None and result["dc_voltage_v"] is not None and result["dc_current_a"] is not None:
            result["dc_power_w"] = round(result["dc_voltage_v"] * result["dc_current_a"], 2)
            result["dc_power_entity"] = "calculated:dc_voltage_x_dc_current"
        return result

    @staticmethod
    def _system_mode(pv_w: float, battery_charge_w: float, battery_discharge_w: float,
                     grid_import_w: float, grid_export_w: float) -> tuple[str, str]:
        if pv_w > PV_ACTIVE_W:
            if battery_charge_w > BATTERY_ACTIVE_W:
                return "Solar + Battery Charging", "Solar is producing and charging the battery."
            if grid_export_w > ACTIVE_W:
                return "Solar Export", "Solar is producing more than the home currently needs."
            return "Solar Producing", "Photovoltaic generation is active."
        if battery_discharge_w > BATTERY_ACTIVE_W:
            return "Battery Support", "Solar is near zero and the battery is supplying the system."
        if battery_charge_w > BATTERY_ACTIVE_W:
            return "Battery Charging", "The battery is charging without meaningful PV production."
        if grid_import_w > ACTIVE_W:
            return "Grid Supply", "The grid is supplying the home."
        return "Idle / Balanced", "No major generation, battery, or grid flow is active."

    @staticmethod
    def _classify_inverter(power: float | None, signals: dict[str, Any], system: dict[str, float]) -> tuple[str, str, bool]:
        raw_status = str(signals.get("raw_status") or "").lower()
        if any(word in raw_status for word in ("fault", "error", "alarm", "failure")):
            return "Fault", "The inverter status entity reports a fault or error.", False
        if any(word in raw_status for word in ("start", "boot", "wake")):
            return "Starting", "The inverter is starting or waking up.", True

        dc_w = float(signals.get("dc_power_w") or 0.0)
        pv_w = system["pv_w"]
        discharge = system["battery_discharge_w"]
        charge = system["battery_charge_w"]
        ac_w = float(power or 0.0)

        if power is not None and ac_w > ACTIVE_W:
            if dc_w > PV_ACTIVE_W or pv_w > PV_ACTIVE_W:
                return "Producing", "AC output is active with meaningful photovoltaic production.", True
            if discharge > BATTERY_ACTIVE_W:
                return "Battery Support", "AC output is active while PV is near zero and the battery is discharging.", True
            if charge > BATTERY_ACTIVE_W:
                return "Battery Charging", "The energy system is charging the battery without meaningful PV production.", True
            return "Active AC", "The inverter has AC output, but the current source cannot be confirmed.", True

        reachable = bool(signals.get("reachable"))
        if power is None and not reachable:
            return "Offline", "No live entity from this Home Assistant device is currently available.", False
        if any(word in raw_status for word in ("sleep", "night")):
            return "Night Standby", "The inverter reports a normal sleeping or night state.", True
        if power is None:
            return "Idle", "The AC power entity is unavailable, but the inverter device remains reachable.", True
        return "Idle", "The inverter is reachable and currently has no meaningful AC output.", True

    def refresh(self) -> dict[str, Any]:
        settings = self.registry.data.setdefault("topology_settings", {})
        site_id = str(settings.get("default_site_id") or "home")
        tolerance = float(settings.get("balance_tolerance_percent") or 10)
        devices = [d for d in self.registry.data.get("devices", []) if isinstance(d, dict) and d.get("enabled", True)]
        inverter_devices = [d for d in devices if self._is_inverter(d)]
        flow = self.energy_flow.summary() or {}
        flows = flow.get("flows") or {}
        system = {
            "pv_w": float(((flows.get("solar_power") or {}).get("w")) or 0.0),
            "battery_charge_w": float(((flows.get("battery_charge_power") or {}).get("w")) or 0.0),
            "battery_discharge_w": float(((flows.get("battery_discharge_power") or {}).get("w")) or 0.0),
            "grid_import_w": float(((flows.get("grid_import_power") or {}).get("w")) or 0.0),
            "grid_export_w": float(((flows.get("grid_export_power") or {}).get("w")) or 0.0),
            "home_w": float(((flows.get("house_power") or {}).get("w")) or 0.0),
        }
        system_mode, system_reason = self._system_mode(
            system["pv_w"], system["battery_charge_w"], system["battery_discharge_w"],
            system["grid_import_w"], system["grid_export_w"]
        )

        rows: list[dict[str, Any]] = []
        total_power = 0.0
        total_energy = 0.0
        healthy_count = 0
        available_energy_count = 0
        for index, device in enumerate(inverter_devices, start=1):
            meta = self._ha_metadata(device)
            power = self._power_w(device.get("power_entity"))
            energy = self._energy_kwh(device.get("energy_entity"))
            signals = self._device_signals(meta.get("source_device_id"))
            model_text = f"{meta.get('model') or ''} {device.get('name') or ''}".lower()
            if "hybrid" in model_text or "gen24" in model_text:
                signals["battery_capable"] = True
            mode, reason, healthy = self._classify_inverter(power, signals, system)
            if healthy:
                healthy_count += 1
            if power is not None:
                total_power += max(power, 0.0)
            if energy is not None:
                total_energy += max(energy, 0.0)
                available_energy_count += 1
            rows.append({
                "id": str(device.get("id") or f"inverter_{index}"),
                "name": str(device.get("name") or f"Inverter {index}"),
                "site_id": str(device.get("site_id") or site_id),
                "power_entity": device.get("power_entity"), "energy_entity": device.get("energy_entity"),
                "power_w": power, "power_kw": round(power / 1000, 3) if power is not None else None,
                "energy_kwh": energy, "energy_type": device.get("energy_type", "auto"),
                "available": healthy, "power_available": power is not None,
                "status": mode, "operating_mode": mode, "mode_reason": reason,
                "dc_power_w": signals.get("dc_power_w"), "dc_voltage_v": signals.get("dc_voltage_v"),
                "dc_current_a": signals.get("dc_current_a"), "dc_power_entity": signals.get("dc_power_entity"),
                "dc_voltage_entity": signals.get("dc_voltage_entity"), "dc_current_entity": signals.get("dc_current_entity"),
                "status_entity": signals.get("status_entity"), "raw_status": signals.get("raw_status"),
                "battery_capable": bool(signals.get("battery_capable")), "share_percent": 0.0,
                "hybrid_inverter": bool(device.get("hybrid_inverter", False)),
                "solar_power_entity": device.get("solar_power_entity"),
                "type": device.get("type", "inverter"), "category": device.get("category", "generation"),
                "room_id": device.get("room_id", "unassigned"), "group_ids": list(device.get("group_ids") or []),
                "state_entity": device.get("state_entity"), "availability_entity": device.get("availability_entity"),
                "priority": device.get("priority", "medium"), "icon": device.get("icon", "mdi:solar-power-variant"),
                "notes": device.get("notes", ""), "enabled": bool(device.get("enabled", True)),
                **meta,
            })

        if total_power > 0:
            for row in rows:
                if row.get("power_w") is not None:
                    row["share_percent"] = round(max(row["power_w"], 0.0) / total_power * 100, 1)

        mapped_solar = ((flows.get("solar_power") or {}).get("w"))
        mismatch_w = mismatch_percent = None
        # A canonical source-first solar mapping is itself valid topology.  Only
        # compare inverter aggregation when physical inverter rows exist; otherwise
        # report the canonical source explicitly instead of implying a missing test.
        balance_status = "Source-first canonical solar" if mapped_solar is not None and not rows else "No solar comparison available"
        if mapped_solar is not None and rows:
            mismatch_w = round(total_power - float(mapped_solar), 2)
            denominator = max(abs(float(mapped_solar)), abs(total_power), 1.0)
            mismatch_percent = round(abs(mismatch_w) / denominator * 100, 1)
            balance_status = "Balanced" if mismatch_percent <= tolerance else "Review"

        nodes = [{"id": site_id, "type": "site", "name": next((s.get("name") for s in self.registry.data.get("sites", []) if s.get("id") == site_id), "Home")}]
        links = []
        for row in rows:
            node_id = f"inverter:{row['id']}"
            nodes.append({"id": node_id, "type": "inverter", "name": row["name"], "power_w": row["power_w"], "status": row["status"]})
            links.append({"from": node_id, "to": "solar:aggregate", "type": "generation"})
        nodes.extend([
            {"id": "solar:aggregate", "type": "solar_total", "name": "Total Solar", "power_w": round(system["pv_w"], 2)},
            {"id": "home:load", "type": "home", "name": "Home Load", "power_w": round(system["home_w"], 2)},
            {"id": "battery:aggregate", "type": "battery", "name": "Battery", "mode": flow.get("flows", {}).get("battery_direction")},
            {"id": "grid:connection", "type": "grid", "name": "Grid"},
        ])
        links.extend([
            {"from": "solar:aggregate", "to": "home:load", "type": "supply"},
            {"from": "solar:aggregate", "to": "battery:aggregate", "type": "charge"},
            {"from": "solar:aggregate", "to": "grid:connection", "type": "export"},
            {"from": "battery:aggregate", "to": "home:load", "type": "supply"},
            {"from": "grid:connection", "to": "home:load", "type": "import"},
        ])

        comparison = []
        producing = [r for r in rows if r.get("power_w") is not None and r.get("power_w", 0) > ACTIVE_W]
        if len(producing) >= 2:
            leader = max(producing, key=lambda x: x.get("power_w") or 0)
            for row in producing:
                if row["id"] == leader["id"] or not leader.get("power_w"):
                    continue
                comparison.append({"inverter_id": row["id"], "name": row["name"], "leader": leader["name"],
                    "power_difference_percent": round((leader["power_w"] - row["power_w"]) / leader["power_w"] * 100, 1),
                    "note": "Differences may reflect array size, orientation, shading, clipping, or sensor timing; this is not a fault diagnosis."})

        self.last = {
            "status": "Ready" if rows else "Not configured", "generated_at": datetime.now(timezone.utc).isoformat(),
            "site_id": site_id, "site_count": len(self.registry.data.get("sites", [])), "inverter_count": len(rows),
            "available_inverter_count": healthy_count, "total_power_w": round(total_power, 2),
            "total_power_kw": round(total_power / 1000, 3), "combined_energy_kwh": round(total_energy, 3) if available_energy_count else None,
            "system_mode": system_mode, "system_mode_reason": system_reason,
            "system_flows": {k: round(v, 2) for k, v in system.items()},
            "inverters": rows[:12], "comparison": comparison[:8],
            "balance": {"status": balance_status, "mapped_total_solar_w": mapped_solar, "inverter_sum_w": round(total_power, 2),
                "difference_w": mismatch_w, "difference_percent": mismatch_percent, "tolerance_percent": tolerance,
                "explanation": ("Short-lived differences can be caused by different sensor update intervals." if mismatch_percent is not None
                    else "Canonical solar is supplied directly by the mapped source; dedicated inverter aggregation is optional in source-first mode." if mapped_solar is not None
                    else "Map total solar power to enable canonical solar diagnostics.")},
            "topology": {"nodes": nodes[:20], "links": links[:24]},
            "aggregation_mode": "registry_inverters_with_system_context_read_only",
            "summary": f"{len(rows)} inverter(s) · {system_mode} · {round(system['pv_w']/1000, 2)} kW PV" if rows else "Add inverter devices from Integration Hub to build the topology.",
            "safety": "Read-only operating-mode analysis. Recommendation Only; no inverter or battery control.", "recorder_safe": True,
        }
        self.event_bus.publish("EnergyTopologyUpdated", "MultiInverterTopologyEngine", {
            "inverter_count": len(rows), "total_power_w": round(total_power, 2), "system_mode": system_mode, "balance": balance_status})
        return self.last

    def summary(self) -> dict[str, Any]:
        return dict(self.last)
