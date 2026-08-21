"""AION EMS Device Import Wizard."""

from __future__ import annotations

from typing import Any


class DeviceImportWizard:
    """Builds real import candidates from Energy Mapping.

    v6.2:
    - Uses Energy Mapping first
    - Merges related entities into logical devices
    - Produces Ready cards for the native Device Manager UI
    """

    SYSTEM_DEVICES = [
        {
            "device_type": "solar_inverter",
            "device_id": "auto_solar_inverter",
            "name": "Solar Inverter",
            "primary_field": "solar_power",
            "extra_fields": ["solar_energy_total"],
            "room_id": "utility",
            "group_ids": ["solar"],
            "category": "generation",
            "priority": "high",
            "icon": "mdi:solar-power-variant",
        },
        {
            "device_type": "battery",
            "device_id": "auto_battery",
            "name": "Battery",
            "primary_field": "battery_power",
            "extra_fields": ["battery_soc", "battery_charge_power", "battery_discharge_power"],
            "room_id": "utility",
            "group_ids": ["battery"],
            "category": "storage",
            "priority": "high",
            "icon": "mdi:battery",
        },
        {
            "device_type": "grid_meter",
            "device_id": "auto_grid_meter",
            "name": "Grid Meter",
            "primary_field": "grid_import_power",
            "extra_fields": ["grid_export_power", "grid_power", "grid_import_energy_total", "grid_export_energy_total"],
            "room_id": "utility",
            "group_ids": [],
            "category": "grid",
            "priority": "critical",
            "icon": "mdi:transmission-tower",
        },
        {
            "device_type": "house",
            "device_id": "auto_house",
            "name": "House Consumption",
            "primary_field": "house_power",
            "extra_fields": ["house_energy_total"],
            "room_id": "utility",
            "group_ids": [],
            "category": "load",
            "priority": "critical",
            "icon": "mdi:home-lightning-bolt",
        },
        {
            "device_type": "ev_charger",
            "device_id": "auto_ev_charger",
            "name": "EV Charger",
            "primary_field": "ev_power",
            "extra_fields": ["ev_energy_total"],
            "room_id": "garage",
            "group_ids": ["ev_charging", "flexible_loads"],
            "category": "vehicle",
            "priority": "high",
            "icon": "mdi:ev-station",
        },
        {
            "device_type": "heat_pump",
            "device_id": "auto_heat_pump",
            "name": "Heat Pump",
            "primary_field": "heat_pump_power",
            "extra_fields": ["heat_pump_energy_total"],
            "room_id": "utility",
            "group_ids": ["heating"],
            "category": "climate",
            "priority": "high",
            "icon": "mdi:heat-pump",
        },
        {
            "device_type": "water_heater",
            "device_id": "auto_water_heater",
            "name": "Water Heater",
            "primary_field": "water_heater_power",
            "extra_fields": ["water_heater_energy_total"],
            "room_id": "utility",
            "group_ids": ["flexible_loads"],
            "category": "flexible_load",
            "priority": "medium",
            "icon": "mdi:water-boiler",
        },
    ]

    APPLIANCES = [
        {
            "device_type": "dishwasher",
            "device_id": "auto_dishwasher",
            "name": "Dishwasher",
            "candidate_key": "dishwasher_candidates",
            "room_id": "kitchen",
            "group_ids": ["flexible_loads"],
            "category": "flexible_load",
            "priority": "medium",
            "icon": "mdi:dishwasher",
        },
        {
            "device_type": "washing_machine",
            "device_id": "auto_washing_machine",
            "name": "Washing Machine",
            "candidate_key": "washing_machine_candidates",
            "room_id": "laundry",
            "group_ids": ["flexible_loads"],
            "category": "flexible_load",
            "priority": "medium",
            "icon": "mdi:washing-machine",
        },
        {
            "device_type": "dryer",
            "device_id": "auto_dryer",
            "name": "Dryer",
            "candidate_key": "dryer_candidates",
            "room_id": "laundry",
            "group_ids": ["flexible_loads"],
            "category": "flexible_load",
            "priority": "medium",
            "icon": "mdi:tumble-dryer",
        },
    ]

    def __init__(self, hass, event_bus, registry, discovery, energy_mapping) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.registry = registry
        self.discovery = discovery
        self.energy_mapping = energy_mapping
        self.last_review: dict[str, Any] = {
            "status": "Not run",
            "message": "Device import review has not run.",
            "ready_count": 0,
            "blocked_count": 0,
            "candidate_count": 0,
            "candidates": [],
            "ready": [],
            "blocked": [],
        }

    def _existing_ids(self) -> set[str]:
        return {device.get("id") for device in self.registry.data.get("devices", [])}

    def _state_exists(self, entity_id: str | None) -> bool:
        return bool(entity_id and self.hass.states.get(entity_id) is not None)

    def _mapping_item(self, mapped: dict[str, Any], field: str) -> dict[str, Any] | None:
        item = mapped.get(field)
        if not item or not item.get("entity_id"):
            return None
        return item

    def _validate_candidate(self, candidate: dict[str, Any], overwrite_existing: bool) -> dict[str, Any]:
        issues = []
        device_id = candidate["device_id"]
        power_entity = candidate.get("power_entity")
        existing = device_id in self._existing_ids()

        if not power_entity:
            issues.append({
                "severity": "error",
                "code": "POWER_ENTITY_MISSING",
                "message": "Power entity is not configured. Open AION EMS > Device Manager, select the Home Assistant power entity manually, then press Test and Save.",
            })
        elif not self._state_exists(power_entity):
            issues.append({
                "severity": "error",
                "code": "POWER_ENTITY_NOT_FOUND",
                "message": f"Primary power entity does not exist: {power_entity}",
            })

        if existing and not overwrite_existing:
            issues.append({
                "severity": "warning",
                "code": "DEVICE_ALREADY_EXISTS",
                "message": f"Device already exists: {device_id}",
            })

        candidate["issues"] = issues
        candidate["already_exists"] = existing
        candidate["status"] = "blocked" if any(i.get("severity") == "error" for i in issues) else "ready"
        return candidate

    def _build_system_candidate(self, meta: dict[str, Any], mapped: dict[str, Any], overwrite_existing: bool) -> dict[str, Any]:
        primary = self._mapping_item(mapped, meta["primary_field"])

        linked_entities = {}
        if primary:
            linked_entities[meta["primary_field"]] = primary.get("entity_id")

        for field in meta.get("extra_fields", []):
            item = self._mapping_item(mapped, field)
            if item:
                linked_entities[field] = item.get("entity_id")

        candidate = {
            "source": "energy_mapping",
            "device_type": meta["device_type"],
            "device_id": meta["device_id"],
            "name": meta["name"],
            "power_entity": primary.get("entity_id") if primary else None,
            "energy_entity": linked_entities.get(meta.get("extra_fields", [""])[0]) if meta.get("extra_fields") else None,
            "linked_entities": linked_entities,
            "room_id": meta["room_id"],
            "group_ids": meta["group_ids"],
            "category": meta["category"],
            "priority": meta["priority"],
            "icon": meta["icon"],
            "mapping_field": meta["primary_field"],
            "score": primary.get("score") if primary else 0,
            "reason": primary.get("reason") if primary else f"missing mapping field {meta['primary_field']}",
        }
        return self._validate_candidate(candidate, overwrite_existing)

    def _build_appliance_candidate(self, meta: dict[str, Any], discovery: dict[str, Any], overwrite_existing: bool) -> dict[str, Any] | None:
        items = discovery.get(meta["candidate_key"], []) or []
        if not items:
            return None

        best = items[0]
        candidate = {
            "source": "smart_discovery",
            "device_type": meta["device_type"],
            "device_id": meta["device_id"],
            "name": meta["name"],
            "power_entity": best.get("entity_id"),
            "energy_entity": None,
            "linked_entities": {meta["candidate_key"]: best.get("entity_id")},
            "room_id": meta["room_id"],
            "group_ids": meta["group_ids"],
            "category": meta["category"],
            "priority": meta["priority"],
            "icon": meta["icon"],
            "score": best.get("score"),
            "reason": best.get("reason"),
        }
        return self._validate_candidate(candidate, overwrite_existing)

    def build_review(self, include_system_devices: bool = True, include_appliances: bool = True, overwrite_existing: bool = False) -> dict[str, Any]:
        self.discovery.refresh()
        self.energy_mapping.refresh()

        mapped = self.energy_mapping.summary().get("mapped", {})
        discovery = self.discovery.summary()

        candidates = []

        if include_system_devices:
            for meta in self.SYSTEM_DEVICES:
                candidates.append(self._build_system_candidate(meta, mapped, overwrite_existing))

        if include_appliances:
            for meta in self.APPLIANCES:
                candidate = self._build_appliance_candidate(meta, discovery, overwrite_existing)
                if candidate:
                    candidates.append(candidate)

        ready = [c for c in candidates if c.get("status") == "ready"]
        blocked = [c for c in candidates if c.get("status") == "blocked"]

        review = {
            "status": "Ready",
            "message": f"Review found {len(ready)} ready candidate(s) and {len(blocked)} blocked candidate(s).",
            "ready_count": len(ready),
            "blocked_count": len(blocked),
            "candidate_count": len(candidates),
            "ready": ready,
            "blocked": blocked,
            "candidates": candidates,
            "safety": "Review only. Registry write requires import action.",
        }

        self.last_review = review
        self.event_bus.publish("DeviceImportReviewUpdated", "DeviceImportWizard", {
            "ready_count": len(ready),
            "blocked_count": len(blocked),
            "candidate_count": len(candidates),
        })
        return review

    async def async_import_reviewed(self, dry_run: bool = True, include_system_devices: bool = True, include_appliances: bool = True, overwrite_existing: bool = False) -> dict[str, Any]:
        review = self.build_review(include_system_devices, include_appliances, overwrite_existing)

        imported = []
        skipped = []

        for candidate in review.get("candidates", []):
            if candidate.get("status") != "ready":
                skipped.append({
                    "device_id": candidate.get("device_id"),
                    "name": candidate.get("name"),
                    "reason": "blocked",
                    "issues": candidate.get("issues", []),
                })
                continue

            if candidate.get("already_exists") and not overwrite_existing:
                skipped.append({
                    "device_id": candidate.get("device_id"),
                    "name": candidate.get("name"),
                    "reason": "already_exists",
                })
                continue

            device = self.registry.build_device(
                device_id=candidate["device_id"],
                name=candidate["name"],
                power_entity=candidate["power_entity"],
                enabled=True,
                device_type=candidate["device_type"],
                category=candidate["category"],
                room_id=candidate["room_id"],
                group_ids=candidate["group_ids"],
                energy_entity=candidate.get("energy_entity"),
                priority=candidate["priority"],
                icon=candidate["icon"],
                notes=f"Imported by AION EMS Device Manager from {candidate['source']}. Linked entities: {candidate.get('linked_entities', {})}",
            )

            # Preserve richer linked entities for future Digital Twin work.
            device["linked_entities"] = candidate.get("linked_entities", {})

            imported.append({
                "device_id": device["id"],
                "name": device["name"],
                "power_entity": device["power_entity"],
                "linked_entities": device.get("linked_entities", {}),
                "dry_run": dry_run,
            })

            if not dry_run:
                await self.registry.async_add_device(device)

        result = {
            "status": "Dry Run" if dry_run else "Imported",
            "message": f"Import finished. Imported: {len(imported)}. Skipped: {len(skipped)}. Dry run: {dry_run}.",
            "imported": imported,
            "skipped": skipped,
            "review": review,
            "ready": review.get("ready", []),
            "blocked": review.get("blocked", []),
            "ready_count": review.get("ready_count", 0),
            "blocked_count": review.get("blocked_count", 0),
            "candidate_count": review.get("candidate_count", 0),
            "safety": "Registry write only. No device control.",
        }

        self.last_review = result
        self.event_bus.publish("ReviewedDevicesImportFinished", "DeviceImportWizard", {
            "dry_run": dry_run,
            "imported_count": len(imported),
            "skipped_count": len(skipped),
        })
        return result

    def summary(self) -> dict[str, Any]:
        return self.last_review
