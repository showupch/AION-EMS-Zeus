"""Opt-in Home Assistant Energy configuration importer for AION EMS Zeus."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from homeassistant.components.energy import async_get_manager


class HomeAssistantEnergyImporter:
    """Preview and explicitly apply Home Assistant Energy configuration.

    Safety contract:
    - Never scans or writes automatically during setup/refresh.
    - Preview is read-only.
    - Apply requires an explicit service/UI action.
    - Existing Zeus mappings/devices are preserved unless overwrite_existing=True.
    """

    TYPE_RULES = (
        ("dishwasher", ("dishwasher", "dish washer", "dish_washer")),
        ("washing_machine", ("washing machine", "washing maschine", "washer")),
        ("dryer", ("dryer", "tumbler", "tumble dryer")),
        ("ev_charger", ("ev charger", "charger", "go-e", "go e", "wallbox")),
        ("water_heater", ("elwa", "water heater", "boiler", "dhw")),
        ("heat_pump", ("heat pump", "heatpump", "wärmepumpe", "waermepumpe", "wlw")),
        ("fridge", ("fridge", "refrigerator", "freezer")),
        ("computer", ("computer", "pc ", "server")),
        ("tv", (" tv", "television", "tv ")),
    )

    def __init__(self, hass, event_bus, registry, energy_mapping) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.registry = registry
        self.energy_mapping = energy_mapping
        self.last_result: dict[str, Any] = {
            "status": "Not run",
            "message": "Home Assistant Energy import has not been scanned.",
            "mode": "opt_in_only",
            "write_performed": False,
            "whole_home": [],
            "individual_devices": [],
        }

    @staticmethod
    def _slug(value: str) -> str:
        value = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
        return value[:64] or "energy_device"

    def _state(self, entity_id: str | None):
        return self.hass.states.get(entity_id) if entity_id else None

    def _entity_info(self, entity_id: str | None) -> dict[str, Any]:
        state = self._state(entity_id)
        attrs = state.attributes if state is not None else {}
        return {
            "entity_id": entity_id,
            "available": bool(state is not None and str(state.state).lower() not in {"unknown", "unavailable", "none", ""}),
            "name": attrs.get("friendly_name") if state is not None else entity_id,
            "unit": attrs.get("unit_of_measurement"),
            "device_class": attrs.get("device_class"),
            "state_class": attrs.get("state_class"),
        }

    def _energy_valid(self, entity_id: str | None) -> bool:
        info = self._entity_info(entity_id)
        return bool(
            info["entity_id"]
            and info["available"]
            and info["unit"] in {"Wh", "kWh", "MWh"}
            and (info["device_class"] in {None, "energy"})
        )

    def _find_power_partner(self, energy_entity: str | None) -> str | None:
        if not energy_entity:
            return None
        energy_state = self._state(energy_entity)
        energy_name = str((energy_state.attributes.get("friendly_name") if energy_state else "") or energy_entity).lower()
        stem = re.sub(r"\b(total|daily|today|energy|charged|consumption|usage|kwh|wh)\b", " ", energy_name)
        stem = " ".join(stem.split())
        entity_stem = re.sub(r"(?:_total)?_?energy(?:_charged)?$", "", energy_entity.split(".", 1)[-1])
        candidates = []
        for state in self.hass.states.async_all("sensor"):
            if state.entity_id.startswith("sensor.aion_ems_zeus_"):
                continue
            attrs = state.attributes
            if attrs.get("device_class") != "power" or attrs.get("unit_of_measurement") not in {"W", "kW"}:
                continue
            name = str(attrs.get("friendly_name") or state.entity_id).lower()
            score = 0
            if entity_stem and entity_stem in state.entity_id:
                score += 100
            words = [x for x in re.split(r"[^a-z0-9]+", stem) if len(x) >= 3]
            score += sum(8 for word in words if word in name or word in state.entity_id.lower())
            if score:
                candidates.append((score, state.entity_id))
        candidates.sort(reverse=True)
        return candidates[0][1] if candidates else None

    def _infer_type(self, name: str, entity_id: str) -> tuple[str, float]:
        text = f" {name} {entity_id} ".lower()
        for device_type, tokens in self.TYPE_RULES:
            if any(token in text for token in tokens):
                return device_type, 0.90
        return "custom", 0.45

    def _existing_match(self, energy_entity: str | None, name: str) -> dict[str, Any] | None:
        name_key = self._slug(name)
        for device in self.registry.data.get("devices", []):
            if energy_entity and str(device.get("energy_entity") or "") == energy_entity:
                return device
            if self._slug(device.get("name") or "") == name_key:
                return device
        return None

    @staticmethod
    def _first_entity(item: Any, keys: tuple[str, ...]) -> str | None:
        if isinstance(item, str):
            return item
        if not isinstance(item, dict):
            return None
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and "." in value:
                return value
        return None

    def _whole_home_candidates(self, prefs: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        seen = set()

        def add(field, entity_id, source, value_kind="energy"):
            if not entity_id or (field, entity_id) in seen:
                return
            seen.add((field, entity_id))
            info = self._entity_info(entity_id)
            if value_kind == "energy":
                valid = self._energy_valid(entity_id)
            elif value_kind == "power":
                valid = bool(
                    info["entity_id"] and info["available"]
                    and info["unit"] in {"W", "kW"}
                    and info["device_class"] in {None, "power"}
                )
            elif value_kind == "soc":
                valid = bool(
                    info["entity_id"] and info["available"]
                    and info["unit"] == "%"
                )
            else:
                valid = bool(info["entity_id"] and info["available"])
            out.append({
                "field": field,
                "source": source,
                "value_kind": value_kind,
                **info,
                "valid": valid,
                "existing_mapping": (self.registry.data.get("entity_mappings") or {}).get(field),
            })

        for item in prefs.get("energy_sources") or []:
            if not isinstance(item, dict):
                continue
            source_type = str(item.get("type") or "").lower()

            if source_type == "grid":
                # Current HA schema: one unified grid source with direct fields.
                add("grid_import_energy_total", item.get("stat_energy_from"), "ha_energy_grid_import")
                add("grid_export_energy_total", item.get("stat_energy_to"), "ha_energy_grid_export")
                add("grid_power", item.get("stat_rate"), "ha_energy_grid_power", "power")

                # Legacy schema: arrays under flow_from/flow_to/power.
                for flow in item.get("flow_from") or []:
                    add("grid_import_energy_total", self._first_entity(flow, ("stat_energy_from", "entity_id")), "ha_energy_grid_import_legacy")
                for flow in item.get("flow_to") or []:
                    add("grid_export_energy_total", self._first_entity(flow, ("stat_energy_to", "entity_id")), "ha_energy_grid_export_legacy")
                for power in item.get("power") or []:
                    if isinstance(power, dict):
                        add("grid_power", power.get("stat_rate"), "ha_energy_grid_power_legacy", "power")

            elif source_type == "solar":
                add("solar_energy_total", item.get("stat_energy_from"), "ha_energy_solar")
                add("solar_power", item.get("stat_rate"), "ha_energy_solar_power", "power")

            elif source_type == "battery":
                # HA semantics: stat_energy_from = battery discharge; stat_energy_to = battery charge.
                add("battery_discharge_energy_total", item.get("stat_energy_from"), "ha_energy_battery_discharge")
                add("battery_charge_energy_total", item.get("stat_energy_to"), "ha_energy_battery_charge")
                add("battery_power", item.get("stat_rate"), "ha_energy_battery_power", "power")
                add("battery_soc", item.get("stat_soc"), "ha_energy_battery_soc", "soc")

                # Compatibility with any older nested battery shape.
                for flow in item.get("flow_from") or []:
                    add("battery_discharge_energy_total", self._first_entity(flow, ("stat_energy_from", "entity_id")), "ha_energy_battery_discharge_legacy")
                for flow in item.get("flow_to") or []:
                    add("battery_charge_energy_total", self._first_entity(flow, ("stat_energy_to", "entity_id")), "ha_energy_battery_charge_legacy")

        return out

    def _device_candidates(self, prefs: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for item in prefs.get("device_consumption") or []:
            if not isinstance(item, dict):
                continue
            entity_id = self._first_entity(item, ("stat_consumption", "entity_id"))
            if not entity_id:
                continue
            info = self._entity_info(entity_id)
            configured_name = str(item.get("name") or "").strip()
            name = configured_name or str(info.get("name") or entity_id)
            device_type, confidence = self._infer_type(name, entity_id)

            configured_rate = self._first_entity(item, ("stat_rate",))
            power_entity = configured_rate
            power_source = "ha_energy_stat_rate" if configured_rate else None
            if not power_entity:
                power_entity = self._find_power_partner(entity_id)
                if power_entity:
                    power_source = "zeus_related_power_discovery"

            existing = self._existing_match(entity_id, name)
            power_info = self._entity_info(power_entity) if power_entity else None
            power_valid = bool(
                power_info
                and power_info.get("available")
                and power_info.get("unit") in {"W", "kW"}
                and power_info.get("device_class") in {None, "power"}
            )
            out.append({
                "device_id": f"ha_energy_{self._slug(name)}",
                "name": name,
                "ha_custom_name": configured_name or None,
                "energy_entity": entity_id,
                "power_entity": power_entity,
                "power_source": power_source,
                "device_type": device_type,
                "type_confidence": confidence,
                "energy_valid": self._energy_valid(entity_id),
                "power_valid": power_valid,
                "energy_info": info,
                "power_info": power_info,
                "already_registered": bool(existing),
                "existing_device_id": existing.get("id") if existing else None,
                "included_in_stat": item.get("included_in_stat"),
                "status": (
                    "already_registered" if existing
                    else "ready" if self._energy_valid(entity_id) and power_valid
                    else "needs_review"
                ),
            })
        return out

    async def async_preview(self) -> dict[str, Any]:
        manager = await async_get_manager(self.hass)
        # Home Assistant EnergyManager exposes preferences as an attribute on
        # current HA releases. Keep compatibility with older/newer manager
        # shapes without mutating the Energy configuration.
        getter = getattr(manager, "async_get_preferences", None)
        if callable(getter):
            prefs = await getter()
        else:
            prefs = getattr(manager, "data", None)
            if prefs is None:
                prefs = getattr(manager, "preferences", None)
            if prefs is None:
                prefs = {}
        prefs = dict(prefs or {})
        raw_sources = list(prefs.get("energy_sources") or [])
        raw_devices = list(prefs.get("device_consumption") or [])
        raw_source_types = {}
        for source in raw_sources:
            if isinstance(source, dict):
                key = str(source.get("type") or "unknown")
                raw_source_types[key] = raw_source_types.get(key, 0) + 1

        whole_home = self._whole_home_candidates(prefs)
        devices = self._device_candidates(prefs)
        result = {
            "status": "Ready",
            "message": f"Found {len(whole_home)} whole-home energy source(s) and {len(devices)} individual electrical device(s) in Home Assistant Energy.",
            "mode": "preview_only",
            "write_performed": False,
            "whole_home": whole_home,
            "individual_devices": devices,
            "ha_energy_diagnostics": {
                "manager_data_present": bool(prefs),
                "preference_keys": sorted(str(k) for k in prefs.keys()),
                "raw_energy_source_count": len(raw_sources),
                "raw_source_types": raw_source_types,
                "raw_device_consumption_count": len(raw_devices),
                "parser_version": "15.3.11-current-and-legacy",
            },
            "counts": {
                "whole_home": len(whole_home),
                "individual_devices": len(devices),
                "already_registered": sum(1 for x in devices if x.get("already_registered")),
                "ready_devices": sum(1 for x in devices if x.get("status") == "ready"),
                "needs_review": sum(1 for x in devices if x.get("status") == "needs_review"),
            },
            "safety": "Preview only. Nothing is imported until the user explicitly confirms Apply.",
        }
        self.last_result = result
        self.event_bus.publish("HomeAssistantEnergyImportPreview", "HAEnergyImporter", result["counts"])
        return result

    async def async_apply(self, import_whole_home: bool = True, import_devices: bool = True, overwrite_existing: bool = False) -> dict[str, Any]:
        preview = await self.async_preview()
        mapped, imported, skipped = [], [], []

        if import_whole_home:
            mappings = self.registry.data.setdefault("entity_mappings", {})
            for row in preview.get("whole_home", []):
                field, entity_id = row.get("field"), row.get("entity_id")
                if not row.get("valid"):
                    skipped.append({"kind": "mapping", "field": field, "entity_id": entity_id, "reason": "invalid_or_unavailable"})
                    continue
                if mappings.get(field) and not overwrite_existing:
                    skipped.append({"kind": "mapping", "field": field, "entity_id": entity_id, "reason": "existing_mapping_preserved"})
                    continue
                mappings[field] = entity_id
                mapped.append({"field": field, "entity_id": entity_id})

        if import_devices:
            for row in preview.get("individual_devices", []):
                if row.get("already_registered") and not overwrite_existing:
                    skipped.append({"kind": "device", "entity_id": row.get("energy_entity"), "reason": "already_registered"})
                    continue
                if not row.get("energy_valid"):
                    skipped.append({"kind": "device", "entity_id": row.get("energy_entity"), "reason": "invalid_energy_sensor"})
                    continue
                if not row.get("power_entity"):
                    skipped.append({"kind": "device", "entity_id": row.get("energy_entity"), "reason": "power_sensor_not_found", "candidate": row})
                    continue
                defaults = {
                    "dishwasher": ("flexible_load", "kitchen", ["flexible_loads"], "medium", "mdi:dishwasher"),
                    "washing_machine": ("flexible_load", "laundry", ["flexible_loads"], "medium", "mdi:washing-machine"),
                    "dryer": ("flexible_load", "laundry", ["flexible_loads"], "medium", "mdi:tumble-dryer"),
                    "ev_charger": ("vehicle", "garage", ["ev_charging", "flexible_loads"], "high", "mdi:ev-station"),
                    "water_heater": ("flexible_load", "utility", ["flexible_loads"], "medium", "mdi:water-boiler"),
                    "heat_pump": ("climate", "utility", ["heating"], "high", "mdi:heat-pump"),
                    "fridge": ("load", "kitchen", [], "medium", "mdi:fridge-outline"),
                    "computer": ("load", "unassigned", [], "medium", "mdi:desktop-tower-monitor"),
                    "tv": ("load", "unassigned", [], "low", "mdi:television"),
                    "custom": ("load", "unassigned", [], "medium", "mdi:power-plug"),
                }
                category, room, groups, priority, icon = defaults.get(row.get("device_type"), defaults["custom"])
                device = self.registry.build_device(
                    device_id=row["device_id"], name=row["name"],
                    power_entity=row["power_entity"], energy_entity=row["energy_entity"],
                    enabled=True, device_type=row["device_type"], category=category,
                    room_id=room, group_ids=groups, priority=priority, icon=icon,
                    notes="Imported explicitly from Home Assistant Energy individual electrical devices.",
                )
                issues = await self.registry.async_add_device(device)
                if any(x.get("severity") == "error" for x in issues):
                    skipped.append({"kind": "device", "entity_id": row.get("energy_entity"), "reason": "registry_validation", "issues": issues})
                else:
                    imported.append({"device_id": device["id"], "name": device["name"], "energy_entity": device["energy_entity"], "power_entity": device["power_entity"]})

        if mapped:
            self.registry.data.setdefault("audit", []).append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "import_home_assistant_energy",
                "mapped_count": len(mapped),
                "device_count": len(imported),
            })
            await self.registry.async_save()

        self.energy_mapping.refresh()
        result = {
            **preview,
            "status": "Imported",
            "message": f"Home Assistant Energy import applied. Mappings: {len(mapped)}. Devices: {len(imported)}. Skipped: {len(skipped)}.",
            "mode": "explicit_apply",
            "write_performed": True,
            "mapped": mapped,
            "imported_devices": imported,
            "skipped": skipped,
            "safety": "Explicit user-confirmed registry/mapping import only. No device control.",
        }
        self.last_result = result
        self.event_bus.publish("HomeAssistantEnergyImportApplied", "HAEnergyImporter", {
            "mapped_count": len(mapped), "device_count": len(imported), "skipped_count": len(skipped)
        })
        return result

    def summary(self) -> dict[str, Any]:
        return self.last_result
