"""AION EMS registry engine."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.helpers.storage import Store

from .const import REGISTRY_STORAGE_KEY, REGISTRY_STORAGE_VERSION


DEFAULT_ROOMS = [
    {"id": "unassigned", "name": "Unassigned", "icon": "mdi:help-circle-outline"},
    {"id": "utility", "name": "Utility", "icon": "mdi:tools"},
    {"id": "kitchen", "name": "Kitchen", "icon": "mdi:silverware-fork-knife"},
    {"id": "laundry", "name": "Laundry", "icon": "mdi:washing-machine"},
    {"id": "garage", "name": "Garage", "icon": "mdi:garage"},
]

DEFAULT_GROUPS = [
    {"id": "solar", "name": "Solar", "category": "generation", "priority": "high", "icon": "mdi:solar-power-variant"},
    {"id": "battery", "name": "Battery", "category": "storage", "priority": "high", "icon": "mdi:battery"},
    {"id": "flexible_loads", "name": "Flexible Loads", "category": "load", "priority": "medium", "icon": "mdi:timer-cog-outline"},
    {"id": "ev_charging", "name": "EV Charging", "category": "vehicle", "priority": "high", "icon": "mdi:ev-station"},
    {"id": "heating", "name": "Heating", "category": "climate", "priority": "high", "icon": "mdi:heat-pump"},
]


class RegistryEngine:
    """Storage-backed registry."""

    def __init__(self, hass, event_bus) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.store = Store(hass, 2, REGISTRY_STORAGE_KEY)
        self.data: dict[str, Any] = {
            "schema_version": 4,
            "devices": [],
            "rooms": DEFAULT_ROOMS,
            "groups": DEFAULT_GROUPS,
            "backups": [],
            "audit": [],
            "entity_mappings": {},
            "sites": [{"id": "home", "name": "Home", "enabled": True, "icon": "mdi:home-lightning-bolt"}],
            "topology_settings": {"default_site_id": "home", "balance_tolerance_percent": 10},
            "home_settings": {"battery_capacity_kwh": None, "owner_name": "", "home_name": "Home", "use_owner_name": True, "story_style": "friendly", "briefing_length": "normal", "data_epoch": None},
            "sources": {"weather": {"entity_id": None, "enabled": False}, "tariffs": {"enabled": False, "currency": "CHF", "import_tariff": None, "export_tariff": None, "standing_charge": 0.0, "vat_included": True}},
        }

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if stored:
            # Preserve older data but ensure required keys exist
            self.data.update(stored)
            self.data.setdefault("devices", [])
            self.data.setdefault("rooms", DEFAULT_ROOMS)
            self.data.setdefault("groups", DEFAULT_GROUPS)
            self.data.setdefault("backups", [])
            self.data.setdefault("audit", [])
            self.data.setdefault("entity_mappings", {})
            self.data.setdefault("sites", [{"id": "home", "name": "Home", "enabled": True, "icon": "mdi:home-lightning-bolt"}])
            self.data.setdefault("topology_settings", {"default_site_id": "home", "balance_tolerance_percent": 10})
            self.data.setdefault("home_settings", {"battery_capacity_kwh": None, "owner_name": "", "home_name": "Home", "use_owner_name": True, "story_style": "friendly", "briefing_length": "normal", "data_epoch": None})
            home_settings = self.data["home_settings"]
            home_settings.setdefault("owner_name", "")
            home_settings.setdefault("home_name", "Home")
            home_settings.setdefault("use_owner_name", True)
            home_settings.setdefault("story_style", "friendly")
            home_settings.setdefault("briefing_length", "normal")
            home_settings.setdefault("data_epoch", None)
            self.data.setdefault("sources", {"weather": {"entity_id": None, "enabled": False}})
            self.data["sources"].setdefault("tariffs", {"enabled": False, "currency": "CHF", "import_tariff": None, "export_tariff": None, "standing_charge": 0.0, "vat_included": True})
        self.data["schema_version"] = 4
        # v9 migration: classify every existing energy entity without deleting data.
        for device in self.data.get("devices", []):
            requested = str(device.get("energy_type") or "auto")
            device["energy_type"] = self.detect_energy_type(device.get("energy_entity"), requested)
            if str(device.get("type") or "").lower() in {"solar_inverter", "inverter", "pv_inverter", "microinverter"}:
                device.setdefault("site_id", "home")
        await self.async_save()
        self.event_bus.publish("RegistryLoaded", "RegistryEngine", self.summary())

    async def async_save(self) -> None:
        await self.store.async_save(self.data)

    def build_device(
        self,
        device_id: str,
        name: str,
        power_entity: str,
        energy_entity: str,
        energy_type: str = "auto",
        enabled: bool = True,
        device_type: str = "custom",
        category: str = "other",
        room_id: str = "unassigned",
        group_ids: list[str] | None = None,
        state_entity: str | None = None,
        availability_entity: str | None = None,
        priority: str = "medium",
        icon: str = "mdi:power-plug",
        notes: str = "",
        hybrid_inverter: bool = False,
        solar_power_entity: str | None = None,
        temperature_entity: str | None = None,
        cop_entity: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": device_id,
            "name": name,
            "enabled": enabled,
            "type": device_type,
            "category": category,
            "room_id": room_id,
            "group_ids": group_ids or [],
            "power_entity": power_entity,
            "energy_entity": energy_entity,
            "energy_type": self.detect_energy_type(energy_entity, energy_type),
            "state_entity": state_entity,
            "availability_entity": availability_entity,
            "priority": priority,
            "icon": icon,
            "controllable": False,
            "automation_entity": None,
            "notes": notes,
            "hybrid_inverter": bool(hybrid_inverter),
            "solar_power_entity": solar_power_entity,
            "temperature_entity": temperature_entity,
            "cop_entity": cop_entity,
            "created_by": "aion_ems",
            "site_id": "home",
        }

    def detect_energy_type(self, entity_id: str | None, requested: str = "auto") -> str:
        """Classify a device energy sensor as daily or total-increasing."""
        requested = str(requested or "auto").lower()
        if requested in {"daily", "total_increasing"}:
            return requested
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return "auto"
        attrs = state.attributes
        state_class = str(attrs.get("state_class") or "").lower()
        friendly = str(attrs.get("friendly_name") or "").lower()
        identifier = f"{str(entity_id).lower()} {friendly}"
        if state_class == "total_increasing":
            return "total_increasing"
        if any(token in identifier for token in ("daily", "today", "day_energy", "energy_day", "day energy")):
            return "daily"
        if attrs.get("last_reset") or state_class == "measurement":
            return "daily"
        if state_class == "total":
            return "total_increasing"
        return "total_increasing"

    def validate_device(self, device: dict[str, Any]) -> list[dict[str, Any]]:
        issues = []
        if not device.get("id"):
            issues.append({"severity": "error", "code": "DEVICE_ID_REQUIRED", "message": "Device ID is required."})
        if not device.get("name"):
            issues.append({"severity": "error", "code": "DEVICE_NAME_REQUIRED", "message": "Device name is required."})
        if not device.get("power_entity"):
            issues.append({"severity": "error", "code": "POWER_ENTITY_REQUIRED", "message": "Power entity is required."})
        if not device.get("energy_entity"):
            issues.append({"severity": "error", "code": "ENERGY_ENTITY_REQUIRED", "message": "Energy entity is required (daily or total increasing)."})
        for key, expected_class, units in (("power_entity", "power", {"W", "kW"}), ("energy_entity", "energy", {"Wh", "kWh", "MWh"})):
            entity_id = device.get(key)
            if not entity_id:
                continue
            if entity_id.startswith(("sensor.aion_ems_zeus_", "binary_sensor.aion_ems_zeus_", "switch.aion_ems_zeus_")):
                issues.append({"severity": "error", "code": "CIRCULAR_AION_MAPPING", "message": f"{key} cannot use an AION output entity."})
                continue
            state = self.hass.states.get(entity_id)
            if state is None:
                issues.append({"severity": "error", "code": "ENTITY_NOT_FOUND", "message": f"{key} entity does not exist."})
                continue
            if str(state.state).lower() in {"unknown", "unavailable", "none", ""}:
                if key == "power_entity" and str(device.get("type") or "") == "solar_inverter":
                    issues.append({"severity": "warning", "code": "INVERTER_POWER_TEMPORARILY_UNAVAILABLE", "message": "Inverter power is currently unavailable; accepted because idle/sleeping inverters may expose no AC power."})
                else:
                    issues.append({"severity": "error", "code": "ENTITY_UNAVAILABLE", "message": f"{key} entity is unavailable."})
            attrs = state.attributes
            unit = attrs.get("unit_of_measurement")
            device_class = attrs.get("device_class")
            if unit not in units:
                issues.append({"severity": "error", "code": "UNIT_MISMATCH", "message": f"{key} has an incompatible unit."})
            elif device_class != expected_class:
                issues.append({"severity": "warning", "code": "DEVICE_CLASS_MISSING_OR_MISMATCH", "message": f"{key} uses a compatible {unit} unit but device class is {device_class or 'missing'}; accepted with warning."})
        energy_type = str(device.get("energy_type") or "auto")
        if energy_type not in {"daily", "total_increasing", "auto"}:
            issues.append({"severity": "error", "code": "ENERGY_TYPE_INVALID", "message": "Energy type must be Auto, Daily, or Total Increasing."})
        temperature_entity = device.get("temperature_entity")
        if temperature_entity:
            if str(temperature_entity).startswith(("sensor.aion_ems_zeus_", "binary_sensor.aion_ems_zeus_", "switch.aion_ems_zeus_")):
                issues.append({"severity": "error", "code": "CIRCULAR_AION_MAPPING", "message": "temperature_entity cannot use an AION output entity."})
            else:
                state = self.hass.states.get(temperature_entity)
                if state is None:
                    issues.append({"severity": "error", "code": "ENTITY_NOT_FOUND", "message": "temperature_entity entity does not exist."})
                else:
                    unit = str(state.attributes.get("unit_of_measurement") or "").strip()
                    device_class = str(state.attributes.get("device_class") or "").strip().lower()
                    if device_class != "temperature" and unit not in {"°C", "C", "°F", "F", "K"}:
                        issues.append({"severity": "error", "code": "TEMPERATURE_ENTITY_INVALID", "message": "temperature_entity must be a Home Assistant temperature sensor (°C, °F, or K)."})
                    elif str(state.state).strip().lower() in {"unknown", "unavailable", "none", ""}:
                        issues.append({"severity": "warning", "code": "TEMPERATURE_TEMPORARILY_UNAVAILABLE", "message": "Temperature sensor is configured but currently unavailable."})
        cop_entity = device.get("cop_entity")
        if cop_entity:
            if str(device.get("type") or "") != "heat_pump":
                issues.append({"severity": "error", "code": "COP_DEVICE_TYPE_INVALID", "message": "cop_entity is only supported for Heat Pump devices."})
            elif str(cop_entity).startswith(("sensor.aion_ems_zeus_", "binary_sensor.aion_ems_zeus_", "switch.aion_ems_zeus_")):
                issues.append({"severity": "error", "code": "CIRCULAR_AION_MAPPING", "message": "cop_entity cannot use an AION output entity."})
            else:
                state = self.hass.states.get(cop_entity)
                if state is None:
                    issues.append({"severity": "error", "code": "ENTITY_NOT_FOUND", "message": "cop_entity entity does not exist."})
                else:
                    unit = str(state.attributes.get("unit_of_measurement") or "").strip()
                    raw = str(state.state).strip().lower()
                    try:
                        value = float(state.state)
                    except (TypeError, ValueError):
                        value = None
                    if raw in {"unknown", "unavailable", "none", ""}:
                        issues.append({"severity": "warning", "code": "COP_TEMPORARILY_UNAVAILABLE", "message": "COP sensor is configured but currently unavailable."})
                    elif value is None:
                        issues.append({"severity": "error", "code": "COP_ENTITY_INVALID", "message": "cop_entity must expose a numeric COP value."})
                    elif unit and unit.lower() not in {"cop", "ratio"}:
                        issues.append({"severity": "warning", "code": "COP_UNIT_UNUSUAL", "message": f"COP sensor unit is '{unit}'. Expected COP or a dimensionless ratio."})
        for key in ("state_entity", "availability_entity"):
            entity_id = device.get(key)
            if not entity_id:
                continue
            if self.hass.states.get(entity_id) is None:
                issues.append({"severity": "error", "code": "ENTITY_NOT_FOUND", "message": f"{key} entity does not exist."})
        return issues

    async def async_add_device(self, device: dict[str, Any]) -> list[dict[str, Any]]:
        issues = self.validate_device(device)
        if any(i["severity"] == "error" for i in issues):
            return issues
        devices = [d for d in self.data["devices"] if d.get("id") != device["id"]]
        devices.append(device)
        self.data["devices"] = devices
        self.data["audit"].append({"timestamp": datetime.now(timezone.utc).isoformat(), "action": "add_device", "device_id": device["id"]})
        await self.async_save()
        self.event_bus.publish("DeviceSaved", "RegistryEngine", {"device_id": device["id"], "name": device["name"]})
        return issues

    async def async_update_device(self, device_id: str, device: dict[str, Any]) -> None:
        device["id"] = device_id
        await self.async_add_device(device)

    async def async_remove_device(self, device_id: str) -> None:
        removed = next((d for d in self.data.get("devices", []) if d.get("id") == device_id), None)
        removed_entities = {str(removed.get(k)) for k in ("power_entity", "energy_entity", "state_entity", "availability_entity", "solar_power_entity", "temperature_entity", "cop_entity") if removed and removed.get(k)}
        self.data["devices"] = [d for d in self.data["devices"] if d.get("id") != device_id]
        # Lifecycle cleanup: mappings owned by the removed device must not survive
        # as permanent stale System Health warnings. Never touch unrelated mappings.
        mappings = self.data.setdefault("entity_mappings", {})
        pruned = [field for field, entity_id in list(mappings.items()) if str(entity_id) in removed_entities]
        for field in pruned:
            mappings.pop(field, None)
        self.data["audit"].append({"timestamp": datetime.now(timezone.utc).isoformat(), "action": "remove_device", "device_id": device_id, "pruned_mappings": pruned})
        await self.async_save()
        self.event_bus.publish("DeviceRemoved", "RegistryEngine", {"device_id": device_id, "pruned_mappings": pruned})

    async def async_backup(self) -> dict[str, Any]:
        backup = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "devices": list(self.data.get("devices", [])),
            "rooms": list(self.data.get("rooms", [])),
            "groups": list(self.data.get("groups", [])),
            "entity_mappings": dict(self.data.get("entity_mappings", {})),
        }
        self.data.setdefault("backups", []).append(backup)
        self.data["backups"] = self.data["backups"][-5:]
        await self.async_save()
        return backup

    async def async_remove_auto_devices(self, dry_run: bool = True) -> list[dict[str, Any]]:
        auto_devices = [d for d in self.data["devices"] if str(d.get("id", "")).startswith("auto_")]
        if not dry_run:
            self.data["devices"] = [d for d in self.data["devices"] if not str(d.get("id", "")).startswith("auto_")]
            await self.async_save()
        return auto_devices

    def summary(self) -> dict[str, Any]:
        return {
            "status": "Ready",
            "schema_version": self.data.get("schema_version", 1),
            "device_count": len(self.data.get("devices", [])),
            "room_count": len(self.data.get("rooms", [])),
            "group_count": len(self.data.get("groups", [])),
            "backup_count": len(self.data.get("backups", [])),
            "audit_count": len(self.data.get("audit", [])),
            "entity_mappings": dict(self.data.get("entity_mappings", {})),
            "devices": self.data.get("devices", [])[:20],
            "rooms": self.data.get("rooms", [])[:20],
            "groups": self.data.get("groups", [])[:20],
            "sites": self.data.get("sites", [])[:10],
            "topology_settings": dict(self.data.get("topology_settings", {})),
            "home_settings": {
                "owner_name": str((self.data.get("home_settings") or {}).get("owner_name") or "")[:80],
                "home_name": str((self.data.get("home_settings") or {}).get("home_name") or "Home")[:80],
                "use_owner_name": (self.data.get("home_settings") or {}).get("use_owner_name", True),
                "story_style": str((self.data.get("home_settings") or {}).get("story_style") or "friendly"),
                "briefing_length": str((self.data.get("home_settings") or {}).get("briefing_length") or "normal"),
                "data_epoch": (self.data.get("home_settings") or {}).get("data_epoch"),
            },
            "safety": "No device control.",
        }
