"""AION EMS pipeline engines."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any
import re

from homeassistant.helpers.storage import Store
from homeassistant.helpers import entity_registry as er, device_registry as dr
from homeassistant.util import dt as dt_util

from .const import DATA_LAKE_STORAGE_KEY, DATA_LAKE_STORAGE_VERSION


class IntegrationHub:
    """Read-only plugin hub, device discovery, notifications and NAS backup status."""

    DOMAINS = ["solar", "battery", "grid", "ev", "heat_pump", "dishwasher", "washing_machine", "dryer", "water_heater"]
    PLUGINS = {
        "email": {"name": "Email", "category": "notifications", "icon": "mdi:email-outline", "permission": "Send notifications only"},
        "pushover": {"name": "Pushover", "category": "notifications", "icon": "mdi:message-badge-outline", "permission": "Send notifications only"},
        "shelly": {"name": "Shelly", "category": "energy_devices", "icon": "mdi:power-socket-eu", "permission": "Read Home Assistant entities"},
        "zigbee": {"name": "Zigbee (ZHA / Zigbee2MQTT)", "category": "energy_devices", "icon": "mdi:zigbee", "permission": "Read Home Assistant entities"},
        "mqtt": {"name": "MQTT Energy Devices", "category": "energy_devices", "icon": "mdi:access-point-network", "permission": "Read Home Assistant entities"},
        "nas_backup": {"name": "Local NAS Backup", "category": "backup", "icon": "mdi:nas", "permission": "Write Zeus backup files to configured mounted folder"},
        "inverter_adapters": {"name": "Inverter Adapters", "category": "energy_devices", "icon": "mdi:solar-power-variant", "permission": "Read Home Assistant entities"},
    }

    def __init__(self, hass, event_bus, registry, discovery) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.registry = registry
        self.discovery = discovery
        self._ha_mount_candidates = []
        self._ha_storage_source = "not_checked"
        self._ha_storage_message = "Home Assistant storage has not been discovered yet."
        self.last = {"status": "Not ready", "quality_score": 0}

    def _settings(self):
        settings = self.registry.data.setdefault("plugin_settings", {})
        for key in self.PLUGINS:
            settings.setdefault(key, {"enabled": False})
        settings.setdefault("safety_mode", "recommendation_only")
        return settings

    def _registry_context(self, state):
        """Return entity/device registry metadata used for reliable discovery."""
        entity_reg = er.async_get(self.hass)
        device_reg = dr.async_get(self.hass)
        entry = entity_reg.async_get(state.entity_id)
        device = device_reg.async_get(entry.device_id) if entry and entry.device_id else None
        identifiers = []
        connections = []
        if device:
            def _flatten(items):
                out=[]
                for item in items or []:
                    if isinstance(item, (tuple, list, set)):
                        out.append(":".join(str(part) for part in item))
                    else:
                        out.append(str(item))
                return out
            identifiers = _flatten(device.identifiers)
            connections = _flatten(device.connections)
        parts = [
            state.entity_id,
            state.attributes.get("friendly_name", ""),
            state.attributes.get("manufacturer", ""),
            state.attributes.get("model", ""),
            state.attributes.get("device_class", ""),
            getattr(entry, "platform", "") if entry else "",
            getattr(entry, "original_name", "") if entry else "",
            getattr(device, "name", "") if device else "",
            getattr(device, "name_by_user", "") if device else "",
            getattr(device, "manufacturer", "") if device else "",
            getattr(device, "model", "") if device else "",
            " ".join(identifiers),
            " ".join(connections),
        ]
        return {
            "text": " ".join(str(x or "") for x in parts).lower(),
            "platform": str(getattr(entry, "platform", "") or "").lower() if entry else "",
            "device_id": entry.device_id if entry else None,
            "device_name": (getattr(device, "name_by_user", None) or getattr(device, "name", None)) if device else None,
            "manufacturer": getattr(device, "manufacturer", None) if device else None,
            "model": getattr(device, "model", None) if device else None,
        }

    def _physical_inverter_candidates(self):
        """Discover inverter candidates from Home Assistant device/entity evidence.

        Device Registry physical devices remain the preferred identity source.
        A strict entity-backed fallback is also allowed for coherent inverter
        measurements (notably Modbus installations) when HA exposes usable power
        and/or energy entities without a dedicated recognized inverter device.
        Derived helper entities never create an inverter identity by themselves.
        """
        inverter_brands = ("goodwe", "sungrow", "fronius", "huawei", "solis", "solax", "deye", "victron", "kostal", "piko", "plenticore")
        entity_reg = er.async_get(self.hass)
        device_reg = dr.async_get(self.hass)

        def flatten(values):
            result = []
            for value in values or []:
                if isinstance(value, (tuple, list, set)):
                    result.append(":".join(str(part) for part in value))
                else:
                    result.append(str(value))
            return result

        def norm_tokens(value):
            value = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
            stop = {"sensor", "binary", "utility", "meter", "day", "daily", "production",
                    "energy", "total", "power", "ac", "dc", "current", "voltage", "status",
                    "inverter", "solar", "device"}
            return {part for part in value.split() if len(part) > 1 and part not in stop}

        entries = list(getattr(entity_reg, "entities", {}).values())
        states_by_id = {state.entity_id: state for state in self.hass.states.async_all()}
        groups = {}

        # First pass: identify real inverter devices directly from Device Registry.
        for device in device_reg.devices:
            identifiers = flatten(getattr(device, "identifiers", None))
            connections = flatten(getattr(device, "connections", None))
            device_entries = [entry for entry in entries if getattr(entry, "device_id", None) == device.id]
            platforms = {str(getattr(entry, "platform", "") or "").lower() for entry in device_entries}
            text = " ".join([
                str(getattr(device, "name_by_user", "") or ""),
                str(getattr(device, "name", "") or ""),
                str(getattr(device, "manufacturer", "") or ""),
                str(getattr(device, "model", "") or ""),
                " ".join(identifiers), " ".join(connections), " ".join(platforms),
                " ".join(str(getattr(entry, "original_name", "") or "") for entry in device_entries),
                " ".join(str(getattr(entry, "entity_id", "") or "") for entry in device_entries),
            ]).lower()
            is_inverter = any(brand in text for brand in inverter_brands) or "inverter" in text or "solar inverter" in text
            if not is_inverter:
                continue
            name = getattr(device, "name_by_user", None) or getattr(device, "name", None) or getattr(device, "model", None) or "Inverter"
            group = {
                "entity_id": None,
                "name": name,
                "device_name": name,
                "platform": "fronius" if "fronius" in text else (next(iter(platforms), "unknown")),
                "manufacturer": getattr(device, "manufacturer", None),
                "model": getattr(device, "model", None),
                "device_id": device.id,
                "identifiers": identifiers[:8],
                "entity_count": 0,
                "physical_entity_count": 0,
                "helper_entity_count": 0,
                "entities": [],
                "entity_options": [],
                "resolution": "device_registry_physical_device",
            }
            seen = set()
            for entry in device_entries:
                entity_id = getattr(entry, "entity_id", None)
                if not entity_id or entity_id in seen:
                    continue
                state = states_by_id.get(entity_id)
                attrs = state.attributes if state else {}
                group["entities"].append(entity_id)
                group["entity_options"].append({
                    "entity_id": entity_id,
                    "name": attrs.get("friendly_name") or getattr(entry, "name", None) or getattr(entry, "original_name", None) or entity_id,
                    "unit": attrs.get("unit_of_measurement"),
                    "unit_of_measurement": attrs.get("unit_of_measurement"),
                    "device_class": attrs.get("device_class") or getattr(entry, "device_class", None),
                    "state_class": attrs.get("state_class"),
                    "state": str(state.state)[:24] if state else "unavailable",
                    "platform": str(getattr(entry, "platform", "") or "unknown"),
                    "source": "physical_device",
                })
                seen.add(entity_id)
            group["entity_id"] = group["entities"][0] if group["entities"] else None
            group["physical_entity_count"] = len(group["entities"])
            groups[device.id] = group

        # Second pass: attach derived helpers to the best matching physical device.
        helpers = []
        helper_platforms = {"utility_meter", "template", "statistics", "integration"}
        for entry in entries:
            platform = str(getattr(entry, "platform", "") or "").lower()
            if platform not in helper_platforms or getattr(entry, "device_id", None) in groups:
                continue
            entity_id = getattr(entry, "entity_id", None)
            if not entity_id:
                continue
            state = states_by_id.get(entity_id)
            attrs = state.attributes if state else {}
            text = " ".join([entity_id, str(attrs.get("friendly_name", "")), str(getattr(entry, "original_name", "") or "")])
            if not any(brand in text.lower() for brand in inverter_brands) and not norm_tokens(text):
                continue
            helpers.append((entry, state, text))

        for entry, state, helper_text in helpers:
            helper_tokens = norm_tokens(helper_text)
            best_group = None
            best_score = 0
            for group in groups.values():
                group_text = " ".join([
                    group.get("name", ""), group.get("manufacturer", "") or "", group.get("model", "") or "",
                    " ".join(group.get("entities", [])), " ".join(group.get("identifiers", [])),
                ])
                group_tokens = norm_tokens(group_text)
                score = len(helper_tokens & group_tokens) * 25
                h = helper_text.lower()
                gname = str(group.get("name") or "").lower()
                model = str(group.get("model") or "").lower()
                if gname and (gname in h or all(tok in h for tok in norm_tokens(gname))):
                    score += 70
                if model and model in h:
                    score += 50
                # Common helper IDs often include a user-friendly inverter alias.
                if any(tok in h for tok in group_tokens if len(tok) >= 4):
                    score += 15
                if score > best_score:
                    best_group, best_score = group, score
            if best_group is None or best_score < 35:
                continue
            entity_id = getattr(entry, "entity_id", None)
            if entity_id in best_group["entities"] or len(best_group["entities"]) >= 60:
                continue
            attrs = state.attributes if state else {}
            best_group["entities"].append(entity_id)
            best_group["entity_options"].append({
                "entity_id": entity_id,
                "name": attrs.get("friendly_name") or getattr(entry, "name", None) or getattr(entry, "original_name", None) or entity_id,
                "unit": attrs.get("unit_of_measurement"),
                "device_class": attrs.get("device_class") or getattr(entry, "device_class", None),
                "state_class": attrs.get("state_class"),
                "state": str(state.state)[:24] if state else "unavailable",
                "platform": str(getattr(entry, "platform", "") or "unknown"),
                "source": "derived_helper",
            })
            best_group["helper_entity_count"] += 1

        # Third pass: entity-backed inverter fallback for generic transports
        # such as Modbus. This is capability discovery, not direct Modbus access.
        helper_only_platforms = {"utility_meter", "template", "statistics", "integration"}
        measurement_terms = {
            "sensor", "binary", "number", "select", "switch",
            "power", "leistung", "energy", "energie", "yield", "production",
            "current", "strom", "voltage", "spannung", "frequency", "frequenz",
            "status", "state", "ac", "dc", "pv", "solar", "inverter",
            "day", "daily", "today", "total", "lifetime", "meter",
            "phase", "l1", "l2", "l3", "w", "kw", "wh", "kwh", "mwh",
        }

        def entity_identity(entry, state):
            attrs = state.attributes if state else {}
            entity_id = str(getattr(entry, "entity_id", "") or "")
            object_id = entity_id.split(".", 1)[-1]
            original = str(getattr(entry, "original_name", "") or "")
            friendly = str(attrs.get("friendly_name", "") or "")
            raw = " ".join([object_id, original, friendly]).lower()
            strong = (
                any(brand in raw for brand in inverter_brands)
                or "solar inverter" in raw
                or "inverter" in raw
            )
            if not strong:
                return None

            object_words = re.findall(r"[a-z]+[0-9]*|[0-9]+", object_id.lower())
            prefix = []
            for word in object_words:
                if word in measurement_terms:
                    break
                prefix.append(word)

            words = re.findall(r"[a-z]+[0-9]*|[0-9]+", raw)
            family = [
                word for word in words
                if word not in measurement_terms and (len(word) >= 2 or word.isdigit())
            ]

            if prefix and any(any(brand in token for brand in inverter_brands) for token in prefix):
                family = prefix
            if not family:
                return None
            return "_".join(family[:6])

        recognized_entity_ids = {
            entity_id
            for group in groups.values()
            for entity_id in group.get("entities", [])
        }
        entity_backed = {}

        for entry in entries:
            entity_id = getattr(entry, "entity_id", None)
            if not entity_id or entity_id in recognized_entity_ids:
                continue
            platform = str(getattr(entry, "platform", "") or "").lower()
            if platform in helper_only_platforms:
                continue
            state = states_by_id.get(entity_id)
            identity = entity_identity(entry, state)
            if not identity:
                continue

            attrs = state.attributes if state else {}
            device_id = getattr(entry, "device_id", None)
            key = f"{platform or 'entity'}:{device_id or identity}"
            group = entity_backed.setdefault(key, {
                "entity_id": None,
                "name": identity.replace("_", " ").title(),
                "device_name": identity.replace("_", " ").title(),
                "platform": platform or "unknown",
                "manufacturer": attrs.get("manufacturer"),
                "model": attrs.get("model"),
                "device_id": device_id,
                "identifiers": [],
                "entity_count": 0,
                "physical_entity_count": 0,
                "helper_entity_count": 0,
                "entities": [],
                "entity_options": [],
                "resolution": "entity_backed_inverter",
                "discovery_evidence": "Home Assistant inverter entities without a recognized dedicated inverter device",
            })
            if entity_id in group["entities"] or len(group["entities"]) >= 60:
                continue
            group["entities"].append(entity_id)
            group["entity_options"].append({
                "entity_id": entity_id,
                "name": attrs.get("friendly_name") or getattr(entry, "original_name", None) or entity_id,
                "unit": attrs.get("unit_of_measurement"),
                "unit_of_measurement": attrs.get("unit_of_measurement"),
                "device_class": attrs.get("device_class") or getattr(entry, "device_class", None),
                "state_class": attrs.get("state_class"),
                "state": str(state.state)[:24] if state else "unavailable",
                "platform": platform or "unknown",
                "source": "entity_backed",
            })
            group["entity_id"] = group["entity_id"] or entity_id

        for key, group in entity_backed.items():
            options = group.get("entity_options", [])
            power_count = sum(
                1 for x in options
                if str(x.get("unit") or "") in {"W", "kW"} or x.get("device_class") == "power"
            )
            energy_count = sum(
                1 for x in options
                if str(x.get("unit") or "") in {"Wh", "kWh", "MWh"} or x.get("device_class") == "energy"
            )
            if not power_count and not energy_count:
                continue
            group["physical_entity_count"] = len(options)
            group["entity_count"] = len(options)
            groups[f"entity:{key}"] = group

        rows = list(groups.values())
        for group in rows:
            options = group.get("entity_options", [])
            group["entity_count"] = len(options)
            group["discovery_summary"] = {
                "entities": len(options),
                "physical": group.get("physical_entity_count", 0),
                "helpers": group.get("helper_entity_count", 0),
                "power": sum(1 for x in options if str(x.get("unit") or "") in {"W", "kW"} or x.get("device_class") == "power"),
                "energy": sum(1 for x in options if str(x.get("unit") or "") in {"Wh", "kWh", "MWh"} or x.get("device_class") == "energy"),
                "voltage": sum(1 for x in options if x.get("device_class") == "voltage" or str(x.get("unit") or "") == "V"),
                "current": sum(1 for x in options if x.get("device_class") == "current" or str(x.get("unit") or "") == "A"),
            }

            def power_score(item):
                text = f"{item.get('entity_id','')} {item.get('name','')}".lower()
                unit = str(item.get("unit") or "")
                score = 0
                if unit in {"W", "kW"}: score += 65
                if item.get("device_class") == "power": score += 30
                if "ac power" in text or "power ac" in text: score += 40
                elif "power" in text: score += 10
                if item.get("source") in {"physical_device", "entity_backed"}: score += 10
                if any(x in text for x in ("energy", "production", "day", "total")): score -= 100
                if any(x in text for x in ("battery", "grid", "load", "photovoltaics", "dc power")): score -= 20
                return score

            def energy_score(item):
                text = f"{item.get('entity_id','')} {item.get('name','')}".lower()
                unit = str(item.get("unit") or "")
                score = 0
                if unit in {"Wh", "kWh", "MWh"}: score += 65
                if item.get("device_class") == "energy": score += 30
                if any(x in text for x in ("energy day", "day production", "daily", "energy_day")): score += 25
                elif any(x in text for x in ("energy", "production", "yield")): score += 10
                if "power" in text and unit in {"W", "kW"}: score -= 100
                return score

            power_ranked = sorted(options, key=power_score, reverse=True)
            energy_ranked = sorted(options, key=energy_score, reverse=True)
            if power_ranked and power_score(power_ranked[0]) > 0:
                group["recommended_power_entity"] = power_ranked[0]["entity_id"]
                group["power_confidence"] = min(100, max(0, power_score(power_ranked[0])))
            if energy_ranked and energy_score(energy_ranked[0]) > 0:
                group["recommended_energy_entity"] = energy_ranked[0]["entity_id"]
                group["energy_confidence"] = min(100, max(0, energy_score(energy_ranked[0])))

        rows.sort(key=lambda x: str(x.get("name") or "").lower())
        return rows[:50]

    def _entity_candidates(self, kind):
        if kind == "inverter_adapters":
            return self._physical_inverter_candidates()
        inverter_brands = ("goodwe", "sungrow", "fronius", "huawei", "solis", "solax", "deye", "victron", "kostal", "piko", "plenticore")
        rows = []
        inverter_devices = {}
        for state in self.hass.states.async_all():
            ctx = self._registry_context(state)
            text, platform = ctx["text"], ctx["platform"]
            is_shelly = platform == "shelly" or "shelly" in text
            is_zha = platform == "zha" or " zha " in f" {text} " or "zigbee" in text
            is_z2m = platform == "mqtt" and ("zigbee2mqtt" in text or "z2m" in text)
            is_zigbee = is_zha or is_z2m
            is_inverter = any(x in text for x in inverter_brands) or "inverter" in text or "solar inverter" in text
            is_mqtt = platform == "mqtt" or " mqtt " in f" {text} "
            matched = (
                kind == "shelly" and is_shelly
                or kind == "zigbee" and is_zigbee
                or kind == "mqtt" and is_mqtt and not is_shelly and not is_zigbee
                or kind == "inverter_adapters" and is_inverter
            )
            if not matched:
                continue
            row = {
                "entity_id": state.entity_id,
                "name": state.attributes.get("friendly_name", state.entity_id),
                "device_name": ctx.get("device_name"),
                "platform": platform or "unknown",
                "manufacturer": ctx.get("manufacturer"),
                "model": ctx.get("model"),
                "device_id": ctx.get("device_id"),
                "unique_id": ctx.get("unique_id"),
            }
            if kind == "inverter_adapters":
                # Count physical inverter devices, not just one integration/platform.
                key = ctx.get("device_id") or state.entity_id
                group = inverter_devices.setdefault(key, {
                    "entity_id": state.entity_id,
                    "name": ctx.get("device_name") or row["name"],
                    "device_name": ctx.get("device_name") or row["name"],
                    "platform": platform or "unknown",
                    "manufacturer": ctx.get("manufacturer"),
                    "model": ctx.get("model"),
                    "device_id": ctx.get("device_id"),
                    "entity_count": 0,
                    "entities": [],
                    "entity_options": [],
                })
                group["entity_count"] += 1
                # Keep all practical inverter entities available to the import UI.
                # Compact metadata avoids making users type entity IDs while keeping
                # the Integration Hub sensor below Recorder limits.
                if len(group["entities"]) < 40:
                    group["entities"].append(state.entity_id)
                    attrs = state.attributes
                    group["entity_options"].append({
                        "entity_id": state.entity_id,
                        "name": attrs.get("friendly_name", state.entity_id),
                        "unit": attrs.get("unit_of_measurement"),
                        "device_class": attrs.get("device_class"),
                        "state_class": attrs.get("state_class"),
                        "state": str(state.state)[:24],
                    })
            else:
                rows.append(row)
        if kind == "inverter_adapters":
            # Helper entities such as utility_meter sensors often have no Home
            # Assistant device_id. Resolve those helpers back to the physical
            # inverter by matching their stable friendly/entity naming against
            # real inverter Device Registry groups, then merge all sibling
            # entities into one import candidate.
            def _norm(value):
                value = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
                stop = {"sensor", "binary", "utility", "meter", "day", "daily",
                        "production", "energy", "total", "power", "ac", "dc",
                        "current", "voltage", "status", "fronius", "inverter"}
                return [part for part in value.split() if part and part not in stop]

            physical = [g for g in inverter_devices.values() if g.get("device_id")]
            orphan_keys = [k for k, g in inverter_devices.items() if not g.get("device_id")]
            for orphan_key in orphan_keys:
                orphan = inverter_devices.get(orphan_key)
                if not orphan:
                    continue
                orphan_tokens = set(_norm(" ".join([
                    orphan.get("name", ""), orphan.get("device_name", ""),
                    " ".join(orphan.get("entities", [])),
                ])))
                best = None
                best_score = 0
                for target in physical:
                    target_tokens = set(_norm(" ".join([
                        target.get("name", ""), target.get("device_name", ""),
                        target.get("manufacturer", ""), target.get("model", ""),
                        " ".join(target.get("entities", [])),
                    ])))
                    overlap = len(orphan_tokens & target_tokens)
                    score = overlap * 20
                    oname = str(orphan.get("name") or "").lower()
                    tname = str(target.get("name") or "").lower()
                    if tname and (tname in oname or oname in tname):
                        score += 60
                    if score > best_score:
                        best, best_score = target, score
                if best is not None and best_score >= 40:
                    seen = set(best.get("entities", []))
                    for entity_id in orphan.get("entities", []):
                        if entity_id not in seen and len(best["entities"]) < 40:
                            best["entities"].append(entity_id)
                            seen.add(entity_id)
                    option_seen = {x.get("entity_id") for x in best.get("entity_options", [])}
                    for option in orphan.get("entity_options", []):
                        if option.get("entity_id") not in option_seen and len(best["entity_options"]) < 40:
                            best["entity_options"].append(option)
                            option_seen.add(option.get("entity_id"))
                    best["entity_count"] = len(best.get("entities", []))
                    best["resolved_helper_entities"] = best.get("resolved_helper_entities", 0) + len(orphan.get("entities", []))
                    best["resolution"] = "physical_device_with_helpers"
                    inverter_devices.pop(orphan_key, None)

            rows = list(inverter_devices.values())
            for group in rows:
                options = group.get("entity_options", [])
                group["entity_count"] = len(group.get("entities", []))
                group["discovery_summary"] = {
                    "entities": len(options),
                    "power": sum(1 for x in options if str(x.get("unit") or "") in {"W", "kW"} or x.get("device_class") == "power"),
                    "energy": sum(1 for x in options if str(x.get("unit") or "") in {"Wh", "kWh", "MWh"} or x.get("device_class") == "energy"),
                    "voltage": sum(1 for x in options if x.get("device_class") == "voltage" or str(x.get("unit") or "") == "V"),
                    "current": sum(1 for x in options if x.get("device_class") == "current" or str(x.get("unit") or "") == "A"),
                }
                options = group.get("entity_options", [])
                def power_score(item):
                    text = f"{item.get('entity_id','')} {item.get('name','')}".lower()
                    unit = str(item.get("unit") or "")
                    score = 0
                    if unit in {"W", "kW"}: score += 60
                    if item.get("device_class") == "power": score += 30
                    if "ac power" in text or "power ac" in text: score += 35
                    elif "power" in text: score += 10
                    if any(x in text for x in ("energy", "production", "day", "total")): score -= 80
                    if any(x in text for x in ("battery", "grid", "load", "photovoltaics", "dc power")): score -= 25
                    return score
                def energy_score(item):
                    text = f"{item.get('entity_id','')} {item.get('name','')}".lower()
                    unit = str(item.get("unit") or "")
                    score = 0
                    if unit in {"Wh", "kWh", "MWh"}: score += 60
                    if item.get("device_class") == "energy": score += 30
                    if any(x in text for x in ("energy day", "day production", "daily", "energy_day")): score += 25
                    elif any(x in text for x in ("energy", "production", "yield")): score += 10
                    if "power" in text and unit in {"W", "kW"}: score -= 80
                    return score
                power_ranked = sorted(options, key=power_score, reverse=True)
                energy_ranked = sorted(options, key=energy_score, reverse=True)
                if power_ranked and power_score(power_ranked[0]) > 0:
                    group["recommended_power_entity"] = power_ranked[0]["entity_id"]
                    group["power_confidence"] = min(100, max(0, power_score(power_ranked[0])))
                if energy_ranked and energy_score(energy_ranked[0]) > 0:
                    group["recommended_energy_entity"] = energy_ranked[0]["entity_id"]
                    group["energy_confidence"] = min(100, max(0, energy_score(energy_ranked[0])))
        rows.sort(key=lambda x: (str(x.get("device_name") or x.get("name") or "").lower(), str(x.get("entity_id") or "")))
        return rows[:100]

    def _notify_services(self):
        """Discover legacy notify services and modern notify entities.

        Home Assistant integrations may expose opaque target names, so unmatched
        notify targets are offered to both Email and Pushover as safe fallbacks.
        """
        result = {"email": [], "pushover": []}
        generic = []
        services = self.hass.services.async_services().get("notify", {})
        for name in sorted(services.keys()):
            low = name.lower()
            candidate = {"service": f"notify.{name}", "name": name.replace("_", " ").title(), "target_type": "service"}
            if any(k in low for k in ("email", "smtp", "mail")):
                result["email"].append(candidate)
            elif "pushover" in low:
                result["pushover"].append(candidate)
            else:
                generic.append(candidate)
        entity_reg = er.async_get(self.hass)
        device_reg = dr.async_get(self.hass)
        for entry in entity_reg.entities.values():
            if entry.domain != "notify":
                continue
            device = device_reg.async_get(entry.device_id) if entry.device_id else None
            text = " ".join(str(x or "") for x in (
                entry.entity_id, entry.platform, entry.original_name,
                getattr(device, "name", "") if device else "",
                getattr(device, "name_by_user", "") if device else "",
                getattr(device, "manufacturer", "") if device else "",
                getattr(device, "model", "") if device else "",
            )).lower()
            label = entry.name or entry.original_name or entry.entity_id
            candidate = {"service": f"entity:{entry.entity_id}", "entity_id": entry.entity_id, "name": label, "target_type": "entity"}
            if any(k in text for k in ("email", "smtp", "mail")):
                result["email"].append(candidate)
            elif "pushover" in text:
                result["pushover"].append(candidate)
            else:
                generic.append(candidate)
        for kind in ("email", "pushover"):
            seen={x.get("service") for x in result[kind]}
            for candidate in generic:
                if candidate.get("service") not in seen:
                    result[kind].append({**candidate, "name": f"{candidate.get('name')} (notify target)"})
                    seen.add(candidate.get("service"))
        return result

    @staticmethod
    def _nas_display_path(cfg):
        """Return the remote NAS address without confusing it with the HA mount."""
        protocol = str(cfg.get("protocol", "smb") or "smb").lower()
        server = str(cfg.get("server", "") or "").strip()
        remote_path = str(cfg.get("remote_path", "") or "").strip()
        share = str(cfg.get("share", "") or "").strip().strip("/\\")
        if not server:
            return ""
        if protocol == "nfs":
            if not remote_path:
                remote_path = f"/{share}" if share else "/"
            if not remote_path.startswith("/"):
                remote_path = "/" + remote_path
            return f"{server}:{remote_path}"
        if remote_path:
            remote_path = remote_path.replace("\\", "/").strip("/")
            return f"//{server}/{remote_path}" if remote_path else f"//{server}"
        return f"//{server}/{share}" if share else f"//{server}"

    @staticmethod
    def _decode_mount_path(value):
        """Decode octal escapes used by Linux mountinfo."""
        replacements = {"\\040": " ", "\\011": "\t", "\\012": "\n", "\\134": "\\"}
        for encoded, decoded in replacements.items():
            value = value.replace(encoded, decoded)
        return value

    @staticmethod
    def _is_ha_storage_path(path_value):
        """Return True only for paths Home Assistant can expose as storage."""
        try:
            from pathlib import Path
            path = str(Path(str(path_value or "")).expanduser())
        except (TypeError, ValueError):
            return False
        return any(path == root or path.startswith(root + "/") for root in ("/media", "/backup", "/share", "/mnt"))

    @staticmethod
    def _mount_match_score(path_value, source, expected_server, expected_remote):
        """Score how likely a HA mount maps to the configured NAS export."""
        from pathlib import Path
        path_text = str(path_value or "").rstrip("/").lower()
        source_text = str(source or "").rstrip("/").lower()
        server = str(expected_server or "").strip().lower()
        remote = str(expected_remote or "").strip().rstrip("/").lower()
        remote_name = Path(remote).name.lower() if remote else ""
        path_name = Path(path_text).name.lower() if path_text else ""
        score = 0
        reasons = []
        if server and server in source_text:
            score += 70
            reasons.append("server")
        if remote and remote in source_text:
            score += 30
            reasons.append("export")
        if remote_name and path_name == remote_name:
            score += 25
            reasons.append("name")
        elif remote_name and remote_name in path_text:
            score += 15
            reasons.append("path")
        return score, ", ".join(reasons)

    def _scan_ha_mounts_sync(self):
        """Discover network storage already mounted and exposed to Home Assistant.

        HA OS normally exposes network storage below /media, /backup or /share.
        Parsing mountinfo finds true NFS/CIFS mounts; scanning those roots is a
        safe fallback for Supervisor bind mounts where the original filesystem
        type is hidden inside the integration container.
        """
        import os
        from pathlib import Path

        cfg = self._settings().get("nas_backup", {})
        expected_server = str(cfg.get("server", "") or "").strip().lower()
        expected_remote = str(cfg.get("remote_path", "") or "").strip().rstrip("/").lower()
        rows = {}

        def add(path_value, fs_type="mounted", source="", discovered_by="filesystem"):
            try:
                path = Path(path_value).expanduser()
                if not path.is_absolute() or not path.exists() or not path.is_dir():
                    return
                resolved = str(path.resolve())
                # Never offer generic container roots as a NAS backup target.
                # Only their mounted child locations belong in the selector.
                if resolved in ("/", "/config", "/media", "/backup", "/share", "/mnt"):
                    return
                stat = os.statvfs(resolved)
                free_bytes = int(stat.f_bavail * stat.f_frsize)
                writable = os.access(resolved, os.W_OK | os.X_OK)
            except (OSError, ValueError):
                return
            # Only expose locations Home Assistant can actually access as storage.
            if not self._is_ha_storage_path(resolved):
                return
            source_text = str(source or "")
            match_score, match_reason = self._mount_match_score(
                resolved, source_text, expected_server, expected_remote
            )
            matched = match_score >= 70
            current = rows.get(resolved)
            candidate = {
                "path": resolved,
                "name": Path(resolved).name or resolved,
                "fs_type": str(fs_type or "mounted"),
                "source": source_text,
                "available": True,
                "writable": writable,
                "free_bytes": free_bytes,
                "free_gb": round(free_bytes / (1024 ** 3), 1),
                "matched": matched,
                "match_score": match_score,
                "match_reason": match_reason,
                "recommended": False,
                "discovered_by": discovered_by,
            }
            if current is None or (candidate["fs_type"] in ("nfs", "nfs4", "cifs", "smb3") and current.get("fs_type") not in ("nfs", "nfs4", "cifs", "smb3")):
                rows[resolved] = candidate

        try:
            mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
            for line in mountinfo.splitlines():
                if " - " not in line:
                    continue
                before, after = line.split(" - ", 1)
                left, right = before.split(), after.split()
                if len(left) < 5 or len(right) < 2:
                    continue
                mount_path = self._decode_mount_path(left[4])
                fs_type, source = right[0].lower(), self._decode_mount_path(right[1])
                network_fs = fs_type in ("nfs", "nfs4", "cifs", "smb3", "fuse.sshfs")
                ha_storage_path = any(mount_path == root or mount_path.startswith(root + "/") for root in ("/media", "/backup", "/share", "/mnt"))
                if network_fs or ha_storage_path:
                    add(mount_path, fs_type, source, "mountinfo")
        except OSError:
            pass

        # Supervisor may expose network storage as bind-mounted directories.
        for base_value in ("/media", "/backup", "/share", "/mnt"):
            base = Path(base_value)
            if not base.exists() or not base.is_dir():
                continue
            try:
                for child in base.iterdir():
                    if child.is_dir() and not child.name.startswith("."):
                        add(child, "ha_storage", "", "home_assistant")
            except OSError:
                continue

        # A saved path is only valid when it is a real HA-accessible storage path.
        # Never re-inject remote Synology paths such as /volume1/hass into this list.
        current_path = str(cfg.get("path", "") or "").strip()
        if current_path and self._is_ha_storage_path(current_path):
            add(current_path, "configured", "", "saved_setting")

        result = list(rows.values())
        writable = [item for item in result if item.get("writable")]
        ranked = sorted(
            writable or result,
            key=lambda item: (-int(item.get("match_score", 0)), item.get("name", "").lower(), item.get("path", "")),
        )
        recommended_path = ""
        if ranked:
            best_score = int(ranked[0].get("match_score", 0))
            if best_score > 0 or len(ranked) == 1:
                recommended_path = ranked[0].get("path", "")
        for item in result:
            item["recommended"] = bool(recommended_path and item.get("path") == recommended_path)
        result.sort(key=lambda item: (not item.get("recommended", False), not item.get("writable", False), -int(item.get("match_score", 0)), item.get("name", "").lower(), item.get("path", "")))
        return result[:50]

    def _discover_ha_mounts(self):
        """Return the last executor-populated mount discovery snapshot."""
        return [dict(item) for item in self._ha_mount_candidates]

    async def _async_supervisor_mounts(self):
        """Read Home Assistant OS network-storage configuration from Supervisor.

        Home Assistant Supervisor is the authority for network storage configured
        under Settings > System > Storage.  The integration only reads this list;
        it never creates, changes, mounts, or removes storage.
        """
        import os
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        token = str(os.environ.get("SUPERVISOR_TOKEN", "") or "").strip()
        if not token:
            return [], "Supervisor storage API is unavailable on this installation."
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                "http://supervisor/mounts",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            ) as response:
                if response.status != 200:
                    return [], f"Home Assistant storage API returned HTTP {response.status}."
                payload = await response.json(content_type=None)
        except Exception as err:  # Network/API availability must not break Zeus startup.
            return [], f"Unable to read Home Assistant network storage: {err}"

        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        mounts = data.get("mounts", []) if isinstance(data, dict) else []
        rows = []
        for item in mounts if isinstance(mounts, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            usage = str(item.get("usage", "") or "").strip().lower()
            mount_type = str(item.get("type", "") or "").strip().lower()
            server = str(item.get("server", "") or "").strip()
            remote = str(item.get("path") or item.get("share") or "").strip()
            state = str(item.get("state", "unknown") or "unknown").lower()
            read_only = bool(item.get("read_only", False))
            # HA exposes media/share mounts to Core under these stable paths.
            # The active backup storage is exposed through /backup.
            if usage == "media" and name:
                local_path = f"/media/{name}"
            elif usage == "share" and name:
                local_path = f"/share/{name}"
            elif usage == "backup":
                local_path = "/backup"
            else:
                local_path = ""
            rows.append({
                "path": local_path,
                "name": name or remote or server or "Network storage",
                "fs_type": mount_type or "network",
                "source": f"{server}:{remote}" if server else remote,
                "server": server,
                "remote_path": remote,
                "usage": usage or "unknown",
                "state": state,
                "available": state in ("active", "mounted", "ok", "unknown"),
                "writable": not read_only and state not in ("failed", "error", "inactive"),
                "read_only": read_only,
                "free_bytes": None,
                "free_gb": None,
                "matched": False,
                "match_score": 0,
                "match_reason": "",
                "recommended": False,
                "discovered_by": "home_assistant_supervisor",
            })
        return rows, "Home Assistant network storage loaded from Supervisor."

    async def async_discover_ha_mounts(self):
        """Discover HA-managed network storage and sanitize the selection."""
        supervisor_rows, message = await self._async_supervisor_mounts()
        filesystem_rows = await self.hass.async_add_executor_job(self._scan_ha_mounts_sync)
        cfg = self._settings().setdefault("nas_backup", {})
        expected_server = str(cfg.get("server", "") or "").strip().lower()
        expected_remote = str(cfg.get("remote_path", "") or "").strip().rstrip("/").lower()

        # Prefer Supervisor records and enrich them with actual filesystem status.
        fs_by_path = {str(x.get("path", "")): x for x in filesystem_rows if x.get("path")}
        combined = []
        for row in supervisor_rows:
            path = str(row.get("path", "") or "")
            fs = fs_by_path.get(path)
            if fs:
                row.update({
                    "available": fs.get("available", row.get("available")),
                    "writable": fs.get("writable", row.get("writable")),
                    "free_bytes": fs.get("free_bytes"),
                    "free_gb": fs.get("free_gb"),
                })
            score, reason = self._mount_match_score(path, row.get("source", ""), expected_server, expected_remote)
            if expected_server and expected_server == str(row.get("server", "") or "").lower():
                score += 100
                reason = (reason + ", supervisor server").strip(", ")
            remote = str(row.get("remote_path", "") or "").rstrip("/").lower()
            if expected_remote and expected_remote == remote:
                score += 80
                reason = (reason + ", supervisor export").strip(", ")
            row["match_score"] = score
            row["match_reason"] = reason
            row["matched"] = score >= 100
            combined.append(row)
        known_paths = {str(x.get("path", "")) for x in combined if x.get("path")}
        combined.extend(x for x in filesystem_rows if str(x.get("path", "")) not in known_paths)
        ranked = sorted(
            [x for x in combined if x.get("path")],
            key=lambda x: (not x.get("writable", False), -int(x.get("match_score", 0)), x.get("name", "").lower()),
        )
        if ranked:
            ranked[0]["recommended"] = bool(ranked[0].get("writable"))
        self._ha_mount_candidates = ranked[:50]
        self._ha_storage_source = "supervisor" if supervisor_rows else "filesystem_fallback"
        self._ha_storage_message = message if supervisor_rows else (
            "No Home Assistant network storage is configured or accessible. "
            "Add it under Settings > System > Storage, then discover again."
        )
        valid_paths = {str(item.get("path", "")) for item in self._ha_mount_candidates}
        selected = str(cfg.get("path", "") or "").strip()
        changed = False
        if selected not in valid_paths:
            # Discard stale or remote NAS paths; they are not HA mount points.
            selected = ""
            if cfg.get("path"):
                cfg["path"] = ""
                changed = True
        if not selected:
            recommended = next((item for item in self._ha_mount_candidates if item.get("recommended") and item.get("writable")), None)
            if recommended:
                cfg["path"] = str(recommended.get("path", ""))
                selected = cfg["path"]
                changed = True
        for item in self._ha_mount_candidates:
            item["selected"] = bool(selected and item.get("path") == selected)
        return self._discover_ha_mounts(), changed

    async def async_refresh_plugin(self, plugin_id: str | None = None):
        """Refresh one plugin without triggering unrelated discovery workflows."""
        plugin_id = str(plugin_id or "").strip()
        if plugin_id == "nas_backup":
            _, changed = await self.async_discover_ha_mounts()
            if changed:
                await self.registry.async_save()
            return self.refresh()

        # Device and notification candidates are read from Home Assistant's current
        # registries/services. Rebuild the compact hub snapshot only; do not scan NAS.
        if plugin_id in {"email", "pushover", "shelly", "zigbee", "mqtt", "inverter_adapters"}:
            return self.refresh()

        return await self.async_refresh()

    async def async_refresh(self):
        """Refresh every plugin discovery source."""
        _, changed = await self.async_discover_ha_mounts()
        if changed:
            await self.registry.async_save()
        return self.refresh()

    def _nas_target_path(self, cfg):
        """Return selected HA mount plus optional Zeus backup subfolder."""
        from pathlib import Path
        mount = Path(str(cfg.get("path", "") or "").strip()).expanduser()
        folder = str(cfg.get("folder", "") or "").strip().strip("/\\")
        return mount / folder if folder else mount

    def _candidate_registry_state(self, candidate):
        """Classify a discovered candidate against the Zeus Registry.

        Discovery often returns several entities for one physical device. A Shelly
        relay, for example, may expose power, energy, over-current and status
        entities. Once any entity from that physical Home Assistant device has
        been imported, every sibling entity must be treated as already added.
        """
        devices = self.registry.data.get("devices", [])
        entity_reg = er.async_get(self.hass)

        candidate_entities = set()
        entity_id = str(candidate.get("entity_id") or "").strip()
        if entity_id:
            candidate_entities.add(entity_id)
        for item in candidate.get("entities", []) or []:
            if item:
                candidate_entities.add(str(item).strip())

        candidate_device_ids = set()
        candidate_device_id = str(candidate.get("device_id") or "").strip()
        if candidate_device_id:
            candidate_device_ids.add(candidate_device_id)
        for candidate_entity in candidate_entities:
            entry = entity_reg.async_get(candidate_entity)
            if entry and entry.device_id:
                candidate_device_ids.add(str(entry.device_id))

        for device in devices:
            mapped = {
                str(device.get("power_entity") or "").strip(),
                str(device.get("energy_entity") or "").strip(),
                str(device.get("state_entity") or "").strip(),
                str(device.get("availability_entity") or "").strip(),
            }
            mapped.discard("")

            mapped_device_ids = set()
            for mapped_entity in mapped:
                entry = entity_reg.async_get(mapped_entity)
                if entry and entry.device_id:
                    mapped_device_ids.add(str(entry.device_id))

            notes = str(device.get("notes") or "")
            same_entity = bool(candidate_entities & mapped)
            same_physical_device = bool(candidate_device_ids & mapped_device_ids)
            same_source = any(
                source_id and f"source_device_id={source_id}" in notes
                for source_id in candidate_device_ids
            )
            if same_entity or same_physical_device or same_source:
                return {
                    "discovery_state": "added",
                    "registry_device_id": device.get("id"),
                    "registry_device_name": device.get("name"),
                    "registry_room_id": device.get("room_id", "unassigned"),
                    "registry_group_ids": device.get("group_ids", []),
                    "matched_by": (
                        "entity" if same_entity else
                        "home_assistant_device" if same_physical_device else
                        "source_device_id"
                    ),
                }
        incomplete = not entity_id and not candidate.get("entities")
        return {"discovery_state": "incomplete" if incomplete else "available"}

    def refresh(self) -> dict[str, Any]:
        disc=self.discovery.summary()
        if disc.get("status") != "Ready": disc=self.discovery.refresh()
        domains={}; ready=0
        for domain in self.DOMAINS:
            cands=disc.get(f"{domain}_candidates",[]); best=cands[0] if cands else None
            available=bool(best and self.hass.states.get(best["entity_id"]) is not None)
            if available: ready+=1
            domains[domain]={"status":"Ready" if available else "Missing","best":best,"candidate_count":len(cands)}
        settings=self._settings(); notify=self._notify_services(); plugins=[]; discovered_total=0
        for key,meta in self.PLUGINS.items():
            cfg=settings.get(key,{})
            candidates=[]
            if key in ("shelly","zigbee","mqtt","inverter_adapters"):
                candidates=self._entity_candidates(key)
                candidates=[{**candidate, **self._candidate_registry_state(candidate)} for candidate in candidates]
            elif key in notify: candidates=notify[key]
            elif key=="nas_backup":
                candidates=self._discover_ha_mounts()
                remote=self._nas_display_path(cfg)
                for candidate in candidates:
                    candidate["remote"] = remote
                    candidate["server"] = cfg.get("server", "")
                    candidate["selected"] = candidate.get("path") == str(cfg.get("path", "") or "")
            discovered_total += len(candidates)
            enabled=bool(cfg.get("enabled"))
            health="Disabled"
            if enabled: health="Ready" if (key=="nas_backup" and cfg.get("server") and cfg.get("path") and any(x.get("selected") and x.get("writable") for x in candidates)) or (key!="nas_backup" and candidates) else "Needs configuration"
            plugins.append({"id":key,**meta,"enabled":enabled,"health":health,"candidate_count":len(candidates),"candidates":candidates,"version":"1.0.1","control_permission":False})
        quality=int((ready/len(self.DOMAINS))*100) if self.DOMAINS else 0
        backup=settings.get("nas_backup",{})
        self.last={"status":"Ready","quality_score":quality,"domains_ready":ready,"domain_count":len(self.DOMAINS),"domains":domains,
          "plugin_count":len(plugins),"enabled_plugin_count":sum(1 for x in plugins if x["enabled"]),"healthy_plugin_count":sum(1 for x in plugins if x["health"]=="Ready"),
          "discovered_candidate_count":discovered_total,"plugins":plugins,"settings":settings,"last_backup":backup.get("last_backup"),"last_backup_status":backup.get("last_backup_status","Never"),
          "ha_storage_source":self._ha_storage_source,"ha_storage_message":self._ha_storage_message,
          "summary":f"{sum(1 for x in plugins if x['enabled'])}/{len(plugins)} plugins enabled. {discovered_total} candidates discovered.",
          "safety_mode":"Recommendation Only","safety":"Read, analyze, back up, and notify only. No device-control permission exists."}
        self.event_bus.publish("IntegrationHubUpdated","IntegrationHub",{"enabled_plugins":self.last["enabled_plugin_count"],"candidates":discovered_total,"safety_mode":"recommendation_only"})
        return self.last

    async def async_save_settings(self, plugin_id, values):
        if plugin_id not in self.PLUGINS: raise ValueError("Unknown plugin")
        safe=dict(values); safe.pop("control",None); safe["control_permission"]=False
        self._settings().setdefault(plugin_id,{}).update(safe)
        await self.registry.async_save(); return self.refresh()


    def _selected_ha_storage(self, cfg):
        """Return the currently selected Home Assistant storage candidate."""
        selected_path = str(cfg.get("path", "") or "").strip()
        if not selected_path:
            return None
        return next(
            (
                dict(item)
                for item in self._ha_mount_candidates
                if str(item.get("path", "") or "").strip() == selected_path
                and bool(item.get("selected", False))
            ),
            None,
        )

    async def _async_create_supervisor_backup(self, cfg, candidate):
        """Create a Home Assistant partial backup in managed backup storage."""
        import os
        from datetime import datetime, timezone, timedelta
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        token = str(os.environ.get("SUPERVISOR_TOKEN", "") or "").strip()
        if not token:
            raise ValueError("Home Assistant Supervisor backup API is unavailable")
        location = str(candidate.get("name", "") or "").strip()
        if not location:
            raise ValueError("The selected Home Assistant backup storage has no storage identity")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        payload = {
            "name": f"AION EMS Zeus {stamp}",
            "homeassistant": True,
            "addons": [],
            "folders": [],
            "compressed": True,
            "location": location,
            "homeassistant_exclude_database": True,
            "background": False,
        }
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(
                "http://supervisor/backups/new/partial",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=300,
            ) as response:
                result = await response.json(content_type=None)
                if response.status not in (200, 201):
                    message = result.get("message") if isinstance(result, dict) else str(result)
                    raise ValueError(f"Home Assistant backup creation failed: {message or 'unknown error'}")
        except ValueError:
            raise
        except Exception as err:
            raise ValueError(f"Unable to create Home Assistant backup: {err}") from err

        data = result.get("data", result) if isinstance(result, dict) else {}
        slug = str(data.get("slug", "") or "") if isinstance(data, dict) else ""
        cfg.update({
            "last_backup": slug or f"Home Assistant backup on {location}",
            "last_backup_status": "Success",
            "last_backup_location": location,
            "last_backup_method": "home_assistant_backup_manager",
            "last_backup_at": datetime.now(timezone.utc).isoformat(),
        })
        await self.registry.async_save()
        self.refresh()
        return slug or location

    async def async_create_nas_backup(self):
        from pathlib import Path
        from datetime import datetime, timezone, timedelta
        import json

        cfg = self._settings().get("nas_backup", {})
        raw = str(cfg.get("path", "") or "").strip()
        if not str(cfg.get("server", "") or "").strip():
            raise ValueError("Configure the NAS server IP address or hostname first")
        if not raw:
            raise ValueError("Discover and select Home Assistant network storage first")

        candidate = self._selected_ha_storage(cfg)
        if candidate and str(candidate.get("usage", "") or "").lower() == "backup":
            if not candidate.get("available", False):
                raise ValueError("The selected Home Assistant backup storage is not active")
            return await self._async_create_supervisor_backup(cfg, candidate)

        mount = Path(raw).expanduser()
        exists = await self.hass.async_add_executor_job(lambda: mount.exists() and mount.is_dir())
        if not exists:
            raise ValueError("The selected Home Assistant storage mount is unavailable. Refresh storage and select it again.")
        path = self._nas_target_path(cfg)
        await self.hass.async_add_executor_job(path.mkdir, parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = path / f"aion_ems_zeus_backup_{stamp}.json"
        payload = {
            "format": "aion_ems_zeus_backup",
            "version": "10.19.2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "safety_mode": "recommendation_only",
            "registry": self.registry.data,
        }
        await self.hass.async_add_executor_job(
            out.write_text, json.dumps(payload, indent=2, default=str), "utf-8"
        )
        keep = max(1, min(100, int(cfg.get("retention", 10) or 10)))

        def apply_retention():
            files = sorted(
                path.glob("aion_ems_zeus_backup_*.json"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for old in files[keep:]:
                try:
                    old.unlink()
                except OSError:
                    pass
            return out.stat().st_size

        size = await self.hass.async_add_executor_job(apply_retention)
        cfg.update({
            "last_backup": str(out),
            "last_backup_status": "Success",
            "last_backup_size_bytes": size,
            "last_backup_method": "direct_mounted_folder",
        })
        await self.registry.async_save()
        self.refresh()
        return str(out)

    async def async_test_nas(self):
        import socket
        from pathlib import Path

        cfg = self._settings().get("nas_backup", {})
        server = str(cfg.get("server", "") or "").strip()
        protocol = str(cfg.get("protocol", "smb") or "smb").lower()
        raw = str(cfg.get("path", "") or "").strip()
        if not server:
            raise ValueError("Enter the NAS server IP address or hostname")
        port = 2049 if protocol == "nfs" else 445

        def check_server():
            with socket.create_connection((server, port), timeout=4):
                return True

        try:
            await self.hass.async_add_executor_job(check_server)
        except OSError as err:
            raise ValueError(f"Cannot reach NAS {server} on port {port}: {err}") from err
        if not raw:
            raise ValueError("NAS server is reachable. Discover and select Home Assistant storage")

        candidate = self._selected_ha_storage(cfg)
        if candidate and str(candidate.get("usage", "") or "").lower() == "backup":
            if not candidate.get("available", False):
                raise ValueError("NAS server is reachable, but Home Assistant reports the selected backup storage as inactive")
            if not candidate.get("writable", False):
                raise ValueError("NAS server is reachable, but the selected Home Assistant backup storage is read-only")
            # Backup storage is owned by Home Assistant's backup manager and may
            # not be exposed as a normal directory to custom integrations.
            # A successful Supervisor discovery plus an active writable state is
            # the correct non-destructive connection test.
            cfg.update({
                "last_test_status": "Success",
                "last_test_method": "home_assistant_backup_manager",
                "last_test_storage": candidate.get("name", ""),
            })
            await self.registry.async_save()
            self.refresh()
            return True

        mount = Path(raw).expanduser()
        exists = await self.hass.async_add_executor_job(lambda: mount.exists() and mount.is_dir())
        if not exists:
            raise ValueError("NAS server is reachable, but the selected Home Assistant mount is unavailable")
        target = self._nas_target_path(cfg)

        def test_write():
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".aion_ems_zeus_write_test"
            probe.write_text("AION EMS Zeus NAS write test", encoding="utf-8")
            probe.unlink()
            return True

        try:
            await self.hass.async_add_executor_job(test_write)
        except OSError as err:
            raise ValueError(f"The selected mount is not writable by Home Assistant: {err}") from err
        cfg.update({
            "last_test_status": "Success",
            "last_test_method": "direct_mounted_folder",
        })
        await self.registry.async_save()
        self.refresh()
        return True

    def recorder_summary(self) -> dict[str, Any]:
        """Return the compact Integration Hub payload safe for Recorder.

        Full discovery candidates stay in runtime memory and are exposed only on
        the dedicated per-plugin discovery entities. Their ``candidates``
        attribute is explicitly excluded from Recorder by PluginDiscoverySensor.
        """
        data = self.last or {}
        plugins = []
        for plugin in data.get("plugins", []) or []:
            plugins.append({
                "id": plugin.get("id"),
                "name": plugin.get("name"),
                "category": plugin.get("category"),
                "enabled": bool(plugin.get("enabled")),
                "health": plugin.get("health"),
                "candidate_count": int(plugin.get("candidate_count", 0) or 0),
                "details_entity": f"sensor.aion_ems_zeus_plugin_{plugin.get('id')}",
            })
        return {
            "status": data.get("status"),
            "quality_score": data.get("quality_score"),
            "domains_ready": data.get("domains_ready"),
            "domain_count": data.get("domain_count"),
            "plugin_count": data.get("plugin_count"),
            "enabled_plugin_count": data.get("enabled_plugin_count"),
            "healthy_plugin_count": data.get("healthy_plugin_count"),
            "discovered_candidate_count": data.get("discovered_candidate_count"),
            "plugins": plugins,
            "last_backup": data.get("last_backup"),
            "last_backup_status": data.get("last_backup_status"),
            "ha_storage_source": data.get("ha_storage_source"),
            "summary": data.get("summary"),
            "safety_mode": data.get("safety_mode"),
            "details_storage": "runtime_memory_and_dedicated_plugin_entities",
            "recorder_safe": True,
        }

    def summary(self): return self.last


class DataBus:
    """Shared live snapshot from normalized Energy Flow and Registry."""

    def __init__(self, event_bus, energy_flow, registry) -> None:
        self.event_bus = event_bus
        self.energy_flow = energy_flow
        self.registry = registry
        self.last = {"status": "Not ready"}

    def refresh(self):
        flow = self.energy_flow.summary()
        if flow.get("status") != "Ready":
            flow = self.energy_flow.refresh()
        self.last = {
            "status": "Ready",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "quality_score": flow.get("quality_score", 0),
            "registry_devices": self.registry.summary().get("device_count", 0),
            "flows": flow.get("flows", {}),
            "available": flow.get("available", {}),
            "registered_devices": flow.get("registered_devices", []),
            "summary": flow.get("summary", "Data Bus ready."),
            "safety": "Read-only normalized snapshot.",
        }
        self.event_bus.publish("DataBusUpdated", "DataBus", {"quality_score": self.last["quality_score"]})
        return self.last

    def summary(self):
        return self.last


class DataLake:
    """Compact minute-level historical energy snapshot store."""

    MAX_SNAPSHOTS = 60 * 24 * 31

    def __init__(self, hass, event_bus, data_bus) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.data_bus = data_bus
        # Reuse the same Energy Mapping instance owned by EnergyFlow/DataBus.
        # DataLake must not expect AionCore to inject a separate attribute.
        self.energy_mapping = data_bus.energy_flow.energy_mapping
        # DataLake needs the shared registry for hybrid-inverter topology checks.
        # DataBus already owns the authoritative registry instance.
        self.registry = data_bus.registry
        self.store = Store(hass, DATA_LAKE_STORAGE_VERSION, DATA_LAKE_STORAGE_KEY)
        self.data = {
            "schema_version": 3,
            "snapshots": [],
            "daily_summaries": {},
            "device_daily_summaries": {},
            "metadata": {"snapshot_retention": "31_days", "daily_summary_retention": "400_days", "auto_capture": "event_driven_with_mapped_energy"},
        }
        self.last = {"status": "Not ready", "snapshot_count": 0}

    async def async_load(self):
        stored = await self.store.async_load()
        if stored:
            self.data.update(stored)
            self.data.setdefault("snapshots", [])
            self.data.setdefault("daily_summaries", {})
            self.data.setdefault("device_daily_summaries", {})
            self.data.setdefault("metadata", {})
        self.data["schema_version"] = 3
        self.data["metadata"].update({"snapshot_retention": "31_days", "daily_summary_retention": "400_days", "auto_capture": "event_driven_with_mapped_energy"})
        await self._async_seed_total_increasing_today()
        await self.async_save()
        self.refresh_summary()



    async def _async_seed_total_increasing_today(self):
        """Seed the active battery operating cycle from cumulative BYD meters.

        The battery page follows the installation's solar cycle. During daylight,
        the active cycle starts at the latest sustained solar-stop boundary. During
        darkness, Zeus keeps the last completed sunset-to-sunset cycle visible so
        the cards do not collapse to only the first few minutes after sunset.

        Recorder cumulative ``state`` values are authoritative. Zeus selects the
        last value at or before the cycle boundary and walks forward, summing only
        positive deltas and handling counter resets safely. Recorder ``change`` is
        used only when cumulative state values are unavailable.
        """
        registry_mappings = dict(
            (getattr(self.registry, "data", {}) or {}).get("entity_mappings", {}) or {}
        )
        try:
            mapping = self.data_bus.energy_flow.mapping.summary()
            runtime_mapped = mapping.get("mapped", {}) if isinstance(mapping, dict) else {}
        except Exception:
            runtime_mapped = {}

        specs = {
            "battery_charge_energy_kwh": "battery_charge_energy_total",
            "battery_discharge_energy_kwh": "battery_discharge_energy_total",
        }
        entity_by_key = {}
        for key, field in specs.items():
            entity_id = registry_mappings.get(field)
            if not entity_id:
                item = runtime_mapped.get(field) or {}
                entity_id = item.get("entity_id")
            if entity_id:
                entity_by_key[key] = str(entity_id)

        metadata = self.data.setdefault("metadata", {})
        trace = metadata["battery_backfill_trace"] = {
            "resolved_entities": dict(entity_by_key),
            "registry_mapping_fields": {field: registry_mappings.get(field) for field in specs.values()},
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        if not entity_by_key:
            trace["status"] = "no_mapped_total_entities"
            return

        now = dt_util.now()
        midnight = dt_util.start_of_local_day(now)
        threshold_w = 50.0
        snapshots = []
        for snap in list(self.data.get("snapshots") or []):
            try:
                stamp = datetime.fromisoformat(str(snap.get("timestamp")))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                local_stamp = dt_util.as_local(stamp)
                if local_stamp < now - timedelta(hours=72):
                    continue
                solar_w = float(((snap.get("flows") or {}).get("solar_power_w")) or 0.0)
            except (TypeError, ValueError):
                continue
            snapshots.append((local_stamp, solar_w))
        snapshots.sort(key=lambda item: item[0])

        # Detect sustained high->low transitions. Five consecutive low samples
        # prevent clouds or brief inverter pauses from becoming a false sunset.
        sunset_boundaries = []
        for idx in range(1, len(snapshots)):
            prev_w = snapshots[idx - 1][1]
            stamp, solar_w = snapshots[idx]
            if prev_w > threshold_w and solar_w <= threshold_w:
                low_window = snapshots[idx:idx + 5]
                if len(low_window) >= 3 and all(w <= threshold_w for _, w in low_window):
                    sunset_boundaries.append(stamp)

        current_solar_w = snapshots[-1][1] if snapshots else None
        if current_solar_w is None:
            try:
                flow = self.data_bus.summary().get("flows", {})
                item = flow.get("solar_power") or {}
                current_solar_w = float(item.get("w")) if isinstance(item, dict) and item.get("w") is not None else None
            except Exception:
                current_solar_w = None

        start = midnight
        cycle_mode = "calendar_day_fallback"
        if sunset_boundaries:
            currently_dark = current_solar_w is not None and current_solar_w <= threshold_w
            if currently_dark and len(sunset_boundaries) >= 2:
                start = sunset_boundaries[-2]
                cycle_mode = "last_completed_solar_cycle"
            else:
                start = sunset_boundaries[-1]
                cycle_mode = "active_solar_cycle"

        # Query one hour before the boundary so the last cumulative state at or
        # before the exact transition is available as the baseline.
        query_start = start - timedelta(hours=1)
        end = now
        response = None
        used_period = None
        last_error = None
        for period in ("5minute", "hour"):
            try:
                response = await self.hass.services.async_call(
                    "recorder", "get_statistics",
                    {
                        "statistic_ids": list(dict.fromkeys(entity_by_key.values())),
                        "start_time": query_start,
                        "end_time": end,
                        "period": period,
                        "types": ["state", "change"],
                        "units": {"energy": "kWh"},
                    },
                    blocking=True,
                    return_response=True,
                )
                used_period = period
                break
            except Exception as err:
                last_error = f"{type(err).__name__}: {err}"
                response = None
        if response is None:
            trace.update({
                "status": "recorder_query_failed",
                "cycle_start": start.isoformat(),
                "cycle_end": end.isoformat(),
                "error": last_error,
            })
            return

        raw = (response or {}).get("statistics", response or {})
        trace.update({
            "status": "recorder_response_received",
            "period": used_period,
            "cycle_start": start.isoformat(),
            "cycle_end": end.isoformat(),
            "cycle_mode": cycle_mode,
            "current_solar_w": current_solar_w,
            "sunset_boundaries": [stamp.isoformat() for stamp in sunset_boundaries[-4:]],
            "response_statistic_ids": sorted(str(key) for key in (raw or {}).keys()),
        })

        day = now.date().isoformat()
        daily = self.data.setdefault("daily_summaries", {}).setdefault(day, {
            "date": day, "snapshot_count": 0, "quality_sum": 0,
            "solar_energy_kwh": 0.0, "house_energy_kwh": 0.0,
            "grid_import_energy_kwh": 0.0, "grid_export_energy_kwh": 0.0,
            "battery_charge_energy_kwh": 0.0, "battery_discharge_energy_kwh": 0.0,
            "solar_true_pv_integrated_kwh": 0.0,
            "peak_solar_power_w": 0.0, "peak_house_power_w": 0.0,
        })
        trackers = metadata.setdefault("battery_total_delta_trackers", {})

        def row_stamp(row):
            raw_stamp = row.get("start") or row.get("end")
            if raw_stamp is None:
                return None
            if isinstance(raw_stamp, datetime):
                stamp = raw_stamp
            else:
                try:
                    stamp = datetime.fromisoformat(str(raw_stamp).replace("Z", "+00:00"))
                except ValueError:
                    return None
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return dt_util.as_local(stamp)

        for energy_key, entity_id in entity_by_key.items():
            rows = [r for r in list((raw or {}).get(entity_id) or []) if isinstance(r, dict)]
            rows.sort(key=lambda r: str(r.get("start") or r.get("end") or ""))
            state_points = []
            fallback_changes = []
            for row in rows:
                stamp = row_stamp(row)
                try:
                    state_value = float(row.get("state"))
                except (TypeError, ValueError):
                    state_value = None
                if stamp is not None and state_value is not None:
                    state_points.append((stamp, state_value))
                try:
                    change = float(row.get("change"))
                except (TypeError, ValueError):
                    change = None
                if stamp is not None and change is not None and stamp >= start and change > 0:
                    fallback_changes.append(change)

            state = self.hass.states.get(entity_id)
            try:
                current = float(state.state) if state and str(state.state).lower() not in {"unknown", "unavailable", "none", ""} else None
            except (TypeError, ValueError):
                current = None
            if current is None:
                continue

            # Baseline is the final cumulative reading at/before cycle start.
            baseline_idx = None
            for idx, (stamp, _) in enumerate(state_points):
                if stamp <= start:
                    baseline_idx = idx
                elif baseline_idx is None:
                    baseline_idx = idx
                    break
                else:
                    break

            reconstructed = 0.0
            reset_count = 0
            method = "recorder_cumulative_state_delta"
            if baseline_idx is not None and state_points:
                sequence = [value for _, value in state_points[baseline_idx:]]
                sequence.append(current)
                previous = sequence[0]
                for value in sequence[1:]:
                    if value + 0.001 >= previous:
                        reconstructed += max(value - previous, 0.0)
                    else:
                        reconstructed += max(value, 0.0)
                        reset_count += 1
                    previous = value
            elif fallback_changes:
                reconstructed = sum(fallback_changes)
                method = "recorder_change_fallback"
            else:
                # No usable history yet: seed only the continuation tracker and
                # wait for the next source change instead of inventing energy.
                reconstructed = float(daily.get(energy_key, 0.0) or 0.0)
                method = "live_delta_waiting_for_history"

            reconstructed = round(max(reconstructed, 0.0), 4)
            daily[energy_key] = reconstructed
            daily[f"{energy_key}_method"] = method
            daily[f"{energy_key}_source"] = entity_id
            daily[f"{energy_key}_seeded_at"] = datetime.now(timezone.utc).isoformat()
            daily[f"{energy_key}_backfill_period"] = used_period
            daily[f"{energy_key}_cycle_start"] = start.isoformat()
            daily[f"{energy_key}_day_definition"] = cycle_mode
            daily[f"{energy_key}_backfill_rows"] = len(rows)
            daily[f"{energy_key}_positive_change_rows"] = len(fallback_changes)
            daily[f"{energy_key}_counter_resets"] = reset_count
            trackers[energy_key] = {
                "entity_id": entity_id,
                "last_value_kwh": current,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "seed_method": method,
                "seeded_day": day,
            }
            trace.setdefault("results", {})[energy_key] = {
                "entity_id": entity_id,
                "reconstructed_kwh": reconstructed,
                "row_count": len(rows),
                "state_point_count": len(state_points),
                "fallback_change_count": len(fallback_changes),
                "current_total_kwh": current,
                "method": method,
                "reset_count": reset_count,
            }


    async def async_save(self):
        await self.store.async_save(self.data)

    @staticmethod
    def _w(flows, key):
        item = flows.get(key)
        return item.get("w") if isinstance(item, dict) else None


    def _mapped_energy_kwh(self, *fields):
        """Return the first available normalized mapped energy reading."""
        try:
            mapping = self.data_bus.energy_flow.mapping.summary()
            mapped = mapping.get("mapped", {}) if isinstance(mapping, dict) else {}
        except Exception:
            mapped = {}
        for field in fields:
            item = mapped.get(field) or {}
            value = item.get("value")
            if isinstance(value, (int, float)):
                return float(value), field, item.get("entity_id")
        return None, None, None

    def _apply_mapped_daily_energy(self, daily, energy_key, today_fields, total_fields, reading):
        """Prefer daily meters; continuously accumulate cumulative battery-meter deltas.

        Battery total-increasing meters are tracked globally in persisted DataLake
        metadata. Every positive source delta is added exactly once to the current
        local-day summary. This makes Zeus battery history independent of inverter
        day boundaries, refreshes, and Home Assistant restarts.
        """
        value, field, entity_id = reading
        if value is None:
            daily.setdefault(f"{energy_key}_method", "power_integration")
            return
        value = max(float(value), 0.0)

        # A configuration export can legally contain the same cumulative entity
        # in both a *_today and *_total slot (for example a vendor only exposes
        # one lifetime battery-charge counter). Never trust the field label alone:
        # when the resolved "today" entity is also mapped as the corresponding
        # total source, normalize it as a cumulative counter instead of exposing
        # the lifetime state as today's energy.
        duplicate_today_total = False
        if field in today_fields and entity_id:
            try:
                mapping = self.data_bus.energy_flow.mapping.summary()
                mapped = mapping.get("mapped", {}) if isinstance(mapping, dict) else {}
            except Exception:
                mapped = {}
            duplicate_today_total = any(
                str((mapped.get(total_field) or {}).get("entity_id") or "").strip() == str(entity_id).strip()
                for total_field in total_fields
            )

        if field in today_fields and not duplicate_today_total:
            daily[energy_key] = round(value, 4)
            daily[f"{energy_key}_method"] = "measured_daily_energy"
            daily[f"{energy_key}_source"] = entity_id
            return
        if duplicate_today_total:
            daily[f"{energy_key}_mapping_normalized"] = "duplicate_today_total_entity"

        # Battery totals use Zeus's own persistent delta recorder. The tracker is
        # global rather than day-local, so a Fronius sunset boundary or a source
        # reset cannot silently restart the daily calculation.
        if energy_key in {"battery_charge_energy_kwh", "battery_discharge_energy_kwh"}:
            metadata = self.data.setdefault("metadata", {})
            trackers = metadata.setdefault("battery_total_delta_trackers", {})
            tracker = trackers.setdefault(energy_key, {})
            previous = tracker.get("last_value_kwh")
            previous_entity = tracker.get("entity_id")
            delta = 0.0
            if previous is not None and previous_entity == entity_id:
                previous = float(previous)
                if value + 0.001 >= previous:
                    delta = max(value - previous, 0.0)
                else:
                    # Counter reset/rollover: the new value is the first segment
                    # after reset. Never subtract already recorded energy.
                    delta = max(value, 0.0)
                    tracker["reset_count"] = int(tracker.get("reset_count", 0) or 0) + 1
                    daily[f"{energy_key}_counter_reset_detected"] = True
            tracker.update({
                "entity_id": entity_id,
                "last_value_kwh": value,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            if delta > 0:
                daily[energy_key] = round(float(daily.get(energy_key, 0.0) or 0.0) + delta, 4)
            else:
                daily.setdefault(energy_key, 0.0)
            daily[f"{energy_key}_method"] = (
                "zeus_persistent_total_delta_recorder_duplicate_today_total"
                if duplicate_today_total else "zeus_persistent_total_delta_recorder"
            )
            daily[f"{energy_key}_source"] = entity_id
            daily[f"{energy_key}_last_delta_kwh"] = round(delta, 6)
            return

        baseline_key = f"_{energy_key}_baseline_kwh"
        latest_key = f"_{energy_key}_latest_kwh"
        carry_key = f"_{energy_key}_carry_kwh"
        baseline = daily.get(baseline_key)
        latest = daily.get(latest_key)
        carry = float(daily.get(carry_key, 0.0) or 0.0)
        if baseline is None:
            baseline = value
            daily[baseline_key] = baseline
        if latest is not None and value + 0.001 < float(latest):
            carry += max(float(latest) - float(baseline), 0.0)
            daily[carry_key] = round(carry, 6)
            baseline = value
            daily[baseline_key] = baseline
            daily[f"{energy_key}_counter_reset_detected"] = True
        daily[latest_key] = value
        daily[energy_key] = round(carry + max(value - float(baseline), 0.0), 4)
        daily[f"{energy_key}_method"] = "measured_total_increasing_continuous_delta"
        daily[f"{energy_key}_source"] = entity_id

    def _integrate_canonical_solar_today(self, daily, entity_id):
        """Continuously integrate the canonical Solar Input into today's PV energy.

        This runs on the normal live refresh path, independent of the 30-minute
        Data Lake snapshot cadence. It intentionally skips long gaps (restart,
        sleep or unavailable source) instead of inventing energy.
        """
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            return 0.0
        state = self.hass.states.get(entity_id)
        if state is None or str(state.state).strip().lower() in {"", "unknown", "unavailable", "none"}:
            return float(daily.get("solar_true_pv_integrated_kwh", 0.0) or 0.0)
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return float(daily.get("solar_true_pv_integrated_kwh", 0.0) or 0.0)
        unit = str(state.attributes.get("unit_of_measurement") or "W").strip().lower()
        if unit == "kw":
            current_w = value * 1000.0
        elif unit == "mw":
            current_w = value * 1_000_000.0
        elif unit == "w":
            current_w = value
        else:
            return float(daily.get("solar_true_pv_integrated_kwh", 0.0) or 0.0)
        current_w = max(current_w, 0.0)
        now = dt_util.now()
        day = now.date().isoformat()
        metadata = self.data.setdefault("metadata", {})
        tracker = metadata.setdefault("canonical_solar_integrator", {})
        previous_entity = str(tracker.get("entity_id") or "")
        previous_day = str(tracker.get("date") or "")
        previous_stamp = tracker.get("timestamp")
        previous_w = tracker.get("power_w")

        # A source change is an explicit accounting boundary. Keep the day's
        # accumulated value only when the same canonical source continues.
        if previous_entity and previous_entity != entity_id:
            daily["solar_true_pv_integrated_kwh"] = 0.0
            daily["solar_input_source_changed_today"] = True
        if previous_day != day or previous_entity != entity_id:
            previous_stamp = None
            previous_w = None

        increment = 0.0
        if previous_stamp is not None and previous_w is not None:
            try:
                last = datetime.fromisoformat(str(previous_stamp))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                elapsed = (now - dt_util.as_local(last)).total_seconds()
            except (TypeError, ValueError):
                elapsed = 0.0
            # Normal UI/coordinator refreshes are far below five minutes. Skip
            # larger gaps so a stale pre-restart value cannot create fake PV.
            if 0.0 < elapsed <= 300.0:
                average_w = (max(float(previous_w), 0.0) + current_w) / 2.0
                increment = average_w * elapsed / 3_600_000.0
                daily["solar_true_pv_integrated_kwh"] = round(
                    float(daily.get("solar_true_pv_integrated_kwh", 0.0) or 0.0) + increment, 6
                )

        tracker.update({
            "entity_id": entity_id,
            "date": day,
            "timestamp": now.isoformat(),
            "power_w": round(current_w, 3),
        })
        daily["solar_true_pv_last_increment_kwh"] = round(increment, 8)
        daily["solar_true_pv_last_power_w"] = round(current_w, 1)
        daily["solar_true_pv_integrator"] = "live_refresh_trapezoid_v1"
        return float(daily.get("solar_true_pv_integrated_kwh", 0.0) or 0.0)

    def refresh_mapped_energy_today(self):
        """Apply authoritative mapped energy sensors to today's summary immediately.

        A valid daily-reset sensor always wins over power integration. Lifetime
        total-increasing sensors are used only when no daily sensor is mapped.
        """
        day = dt_util.now().date().isoformat()
        daily = self.data.setdefault("daily_summaries", {}).setdefault(day, {
            "date": day, "snapshot_count": 0, "quality_sum": 0,
            "solar_energy_kwh": 0.0, "house_energy_kwh": 0.0,
            "grid_import_energy_kwh": 0.0, "grid_export_energy_kwh": 0.0,
            "battery_charge_energy_kwh": 0.0, "battery_discharge_energy_kwh": 0.0,
            "solar_true_pv_integrated_kwh": 0.0,
            "peak_solar_power_w": 0.0, "peak_house_power_w": 0.0,
        })
        mapped_specs = [
            ("solar_energy_kwh", ("solar_energy_today",), ("solar_energy_total",)),
            ("house_energy_kwh", ("house_energy_today",), ("house_energy_total",)),
            ("grid_import_energy_kwh", ("grid_import_energy_today",), ("grid_import_energy_total",)),
            ("grid_export_energy_kwh", ("grid_export_energy_today",), ("grid_export_energy_total",)),
            ("battery_charge_energy_kwh", ("battery_charge_energy_today",), ("battery_charge_energy_total", "battery_energy_total")),
            ("battery_discharge_energy_kwh", ("battery_discharge_energy_today",), ("battery_discharge_energy_total", "battery_energy_total")),
        ]
        for energy_key, today_fields, total_fields in mapped_specs:
            reading = self._mapped_energy_kwh(*(today_fields + total_fields))
            self._apply_mapped_daily_energy(daily, energy_key, today_fields, total_fields, reading)
        devices = (getattr(self.registry, "data", {}) or {}).get("devices", [])
        hybrid_devices = [d for d in devices if isinstance(d, dict) and bool(d.get("hybrid_inverter"))]
        hybrid_enabled = bool(hybrid_devices)
        # Canonical Solar Input is owned by Energy Sources mapping, not devices.
        dedicated_pv_entity = str((self.energy_mapping.mappings or {}).get("solar_power") or "").strip()
        dedicated_true_pv_configured = bool(dedicated_pv_entity)
        mapped_daily_solar = self._mapped_energy_kwh("solar_energy_today")
        if dedicated_true_pv_configured and mapped_daily_solar[0] is not None:
            # A valid daily-reset Solar energy mapping is the authoritative
            # current-calendar-day True-PV total. The mapped Solar power entity
            # remains authoritative for instantaneous PV power and is the fallback
            # integration source only when no daily Solar energy meter exists.
            mapped_value, _mapped_field, mapped_entity = mapped_daily_solar
            daily["solar_energy_kwh"] = round(max(float(mapped_value), 0.0), 4)
            daily["solar_energy_kwh_method"] = "mapped_true_pv_daily_energy"
            daily["solar_energy_kwh_source"] = mapped_entity
            daily["solar_true_pv_daily_authority"] = True
            daily["solar_energy_raw_ac_kwh"] = round(max(float(mapped_value), 0.0), 4)
        elif dedicated_true_pv_configured:
            # Integrate canonical PV on every live refresh. Finance and Analytics
            # therefore have a continuously advancing Today value even between
            # the slower persisted Data Lake snapshots.
            self._integrate_canonical_solar_today(daily, dedicated_pv_entity)
            # HYBRID canonical rule: inverter AC energy may contain battery discharge,
            # so it is never authoritative solar energy. The dedicated true-PV
            # power sensor is integrated independently by DataLake and owns solar.
            raw_mapped_solar = float(daily.get("solar_energy_kwh", 0.0) or 0.0)
            true_pv = float(daily.get("solar_true_pv_integrated_kwh", 0.0) or 0.0)
            daily["solar_energy_raw_ac_kwh"] = round(raw_mapped_solar, 4)
            canonical_solar = max(0.0, true_pv)

            # When the canonical Solar Input is introduced or changed part-way
            # through the local day, the live integrator only covers the time
            # since that accounting boundary. Do not publish a partial-day PV
            # total as if it represented the whole calendar day. If all site
            # boundary energy meters are available, reconstruct the missing
            # part from conservation of energy:
            #   PV = Home + Export + Battery charge - Import - Battery discharge
            # This backfill is used only for the transition day. From the next
            # local midnight the canonical Solar Input integration owns the full
            # day again.
            source_changed_today = bool(daily.get("solar_input_source_changed_today"))
            boundary_fields = (
                "house_energy_kwh", "grid_export_energy_kwh",
                "battery_charge_energy_kwh", "grid_import_energy_kwh",
                "battery_discharge_energy_kwh",
            )
            mappings = dict(self.energy_mapping.mappings or {})
            other_generation_configured = any(
                str(mappings.get(field) or "").strip()
                for field in ("wind_power", "generator_power")
            )
            boundary_solar = None
            if not other_generation_configured and all(daily.get(key) is not None for key in boundary_fields):
                boundary_solar = max(0.0, (
                    float(daily.get("house_energy_kwh", 0.0) or 0.0)
                    + float(daily.get("grid_export_energy_kwh", 0.0) or 0.0)
                    + float(daily.get("battery_charge_energy_kwh", 0.0) or 0.0)
                    - float(daily.get("grid_import_energy_kwh", 0.0) or 0.0)
                    - float(daily.get("battery_discharge_energy_kwh", 0.0) or 0.0)
                ))
                daily["solar_energy_boundary_reference_kwh"] = round(boundary_solar, 4)

            # True-PV power remains the canonical source. Its live trapezoid
            # integrator deliberately skips stale gaps >5 minutes; that protects
            # against invented energy, but can leave Today's PV materially short
            # after HA restarts or missed refreshes. On a solar-only generation
            # site the measured Home/Grid/Battery boundary provides a conservative
            # conservation-of-energy floor. Recover only a material deficit so
            # normal meter timestamp skew cannot make the value chatter.
            if boundary_solar is not None:
                deficit = boundary_solar - canonical_solar
                tolerance = max(0.50, boundary_solar * 0.02)
                if deficit > tolerance:
                    canonical_solar = boundary_solar
                    daily["solar_energy_boundary_backfill_kwh"] = round(boundary_solar, 4)
                    daily["solar_energy_boundary_recovered_kwh"] = round(deficit, 4)
                    daily["solar_energy_boundary_recovery_reason"] = (
                        "source_change" if source_changed_today else "missed_true_pv_integration_intervals"
                    )
                    daily["solar_energy_kwh_method"] = "inputs_solar_power_integration_with_boundary_recovery"
                    daily["solar_energy_kwh_source"] = dedicated_pv_entity
                    # Keep the persisted integrator caught up with the recovered
                    # canonical Today total, otherwise every refresh would report
                    # the same missing interval again.
                    daily["solar_true_pv_integrated_kwh"] = round(boundary_solar, 6)
            elif other_generation_configured:
                daily["solar_energy_boundary_recovery_suppressed"] = "other_generation_configured"

            daily["solar_energy_kwh"] = round(max(0.0, canonical_solar), 4)
            daily["solar_hybrid_correction_kwh"] = round(max(0.0, raw_mapped_solar - true_pv), 4)
            if "solar_energy_kwh_method" not in daily or not str(daily.get("solar_energy_kwh_method") or "").startswith("inputs_solar_boundary_backfill"):
                daily["solar_energy_kwh_method"] = "inputs_solar_power_integration"
                daily["solar_energy_kwh_source"] = dedicated_pv_entity
        elif hybrid_enabled:
            # Compatibility fallback only for old HYBRID configurations without
            # the dedicated true-PV sensor. New configurations should never rely
            # on this estimate.
            raw_solar = float(daily.get("solar_energy_kwh", 0.0) or 0.0)
            discharge = float(daily.get("battery_discharge_energy_kwh", 0.0) or 0.0)
            daily["solar_energy_raw_ac_kwh"] = round(raw_solar, 4)
            daily["solar_hybrid_correction_kwh"] = round(min(raw_solar, discharge), 4)
            daily["solar_energy_kwh"] = round(max(0.0, raw_solar - discharge), 4)
            daily["solar_energy_kwh_method"] = "hybrid_legacy_ac_minus_discharge_fallback"
            daily["solar_energy_kwh_source"] = "mapped_solar_minus_battery_discharge"
        daily["hybrid_inverter_correction_active"] = hybrid_enabled
        daily["hybrid_true_pv_configured"] = dedicated_true_pv_configured
        daily["dedicated_true_pv_configured"] = dedicated_true_pv_configured
        daily["dedicated_pv_entity"] = dedicated_pv_entity or None
        daily["energy_mapping_v2"] = True
        return daily

    def _device_snapshot(self, device):
        """Capture power plus a normalized energy reading for one registered device."""
        out = {"id": device.get("id"), "name": device.get("name"), "type": device.get("type"),
               "power_w": device.get("power_w"), "energy_entity": device.get("energy_entity"),
               "energy_type": device.get("energy_type", "auto")}
        entity_id = device.get("energy_entity")
        state = self.hass.states.get(entity_id) if entity_id else None
        value = None
        if state and str(state.state).lower() not in {"unknown", "unavailable", "none", ""}:
            try:
                value = float(state.state)
                unit = str(state.attributes.get("unit_of_measurement") or "").lower()
                if unit == "wh": value /= 1000.0
                elif unit == "mwh": value *= 1000.0
                elif unit != "kwh": value = None
            except (TypeError, ValueError):
                value = None
        out["energy_value_kwh"] = value
        return out

    async def async_capture_snapshot(self):
        bus = self.data_bus.summary()
        if bus.get("status") != "Ready":
            bus = self.data_bus.refresh()
        now = datetime.now(timezone.utc)
        flows = bus.get("flows", {})
        compact_flows = {
            "solar_power_w": self._w(flows, "solar_power"),
            "house_power_w": self._w(flows, "house_power"),
            "grid_import_power_w": self._w(flows, "grid_import_power"),
            "grid_export_power_w": self._w(flows, "grid_export_power"),
            "battery_charge_power_w": self._w(flows, "battery_charge_power"),
            "battery_discharge_power_w": self._w(flows, "battery_discharge_power"),
            "battery_soc_percent": flows.get("battery_soc_percent"),
            "known_major_loads_power_w": self._w(flows, "known_major_loads_power"),
        }
        previous = self.data["snapshots"][-1] if self.data.get("snapshots") else None
        interval_seconds = 60.0
        if previous:
            try:
                interval_seconds = min(300.0, max(1.0, (now - datetime.fromisoformat(previous["timestamp"])).total_seconds()))
            except (KeyError, TypeError, ValueError):
                pass
        snap = {
            "timestamp": now.isoformat(),
            "quality_score": bus.get("quality_score", 0),
            "registry_devices": bus.get("registry_devices", 0),
            "interval_seconds": round(interval_seconds, 1),
            "flows": compact_flows,
            "devices": [self._device_snapshot(d) for d in bus.get("registered_devices", [])],
        }
        self.data["snapshots"].append(snap)
        self.data["snapshots"] = self.data["snapshots"][-self.MAX_SNAPSHOTS:]
        day = snap["timestamp"][:10]
        daily = self.data["daily_summaries"].setdefault(day, {
            "date": day, "snapshot_count": 0, "quality_sum": 0,
            "solar_energy_kwh": 0.0, "house_energy_kwh": 0.0,
            "grid_import_energy_kwh": 0.0, "grid_export_energy_kwh": 0.0,
            "battery_charge_energy_kwh": 0.0, "battery_discharge_energy_kwh": 0.0,
            "solar_true_pv_integrated_kwh": 0.0,
            "peak_solar_power_w": 0.0, "peak_house_power_w": 0.0,
        })
        daily["snapshot_count"] += 1
        daily["quality_sum"] += snap["quality_score"]
        daily["avg_quality_score"] = round(daily["quality_sum"] / daily["snapshot_count"], 1)
        hours = interval_seconds / 3600.0
        pairs = [
            ("solar_power_w", "solar_energy_kwh"), ("house_power_w", "house_energy_kwh"),
            ("grid_import_power_w", "grid_import_energy_kwh"), ("grid_export_power_w", "grid_export_energy_kwh"),
            ("battery_charge_power_w", "battery_charge_energy_kwh"), ("battery_discharge_power_w", "battery_discharge_energy_kwh"),
        ]
        for power_key, energy_key in pairs:
            value = compact_flows.get(power_key)
            if isinstance(value, (int, float)):
                increment = max(value, 0) * hours / 1000.0
                daily[energy_key] = round(daily.get(energy_key, 0.0) + increment, 4)
                if power_key == "solar_power_w" and not str((self.energy_mapping.mappings or {}).get("solar_power") or "").strip():
                    # Legacy fallback only when no canonical Solar Input exists.
                    # Canonical Inputs are integrated continuously by
                    # _integrate_canonical_solar_today() on the live refresh path,
                    # so the slower snapshot must not count the same PV twice.
                    daily["solar_true_pv_integrated_kwh"] = round(
                        float(daily.get("solar_true_pv_integrated_kwh", 0.0) or 0.0) + increment, 4
                    )
        # Energy Mapping Engine v2: measured daily/lifetime energy overrides sparse
        # power integration. This keeps Dashboard, Finance and Reports consistent.
        mapped_specs = [
            ("solar_energy_kwh", ("solar_energy_today",), ("solar_energy_total",)),
            ("house_energy_kwh", ("house_energy_today",), ("house_energy_total",)),
            ("grid_import_energy_kwh", ("grid_import_energy_today",), ("grid_import_energy_total",)),
            ("grid_export_energy_kwh", ("grid_export_energy_today",), ("grid_export_energy_total",)),
            ("battery_charge_energy_kwh", ("battery_charge_energy_today",), ("battery_charge_energy_total", "battery_energy_total")),
            ("battery_discharge_energy_kwh", ("battery_discharge_energy_today",), ("battery_discharge_energy_total", "battery_energy_total")),
        ]
        for energy_key, today_fields, total_fields in mapped_specs:
            reading = self._mapped_energy_kwh(*(today_fields + total_fields))
            self._apply_mapped_daily_energy(daily, energy_key, today_fields, total_fields, reading)
        devices = (getattr(self.registry, "data", {}) or {}).get("devices", [])
        hybrid_devices = [d for d in devices if isinstance(d, dict) and bool(d.get("hybrid_inverter"))]
        # Canonical Solar Input is owned by Energy Sources mapping, not devices.
        dedicated_pv_entity = str((self.energy_mapping.mappings or {}).get("solar_power") or "").strip()
        mapped_daily_solar = self._mapped_energy_kwh("solar_energy_today")
        if dedicated_pv_entity and mapped_daily_solar[0] is not None:
            mapped_value, _mapped_field, mapped_entity = mapped_daily_solar
            daily["solar_energy_kwh"] = round(max(float(mapped_value), 0.0), 4)
            daily["solar_energy_kwh_method"] = "mapped_true_pv_daily_energy"
            daily["solar_energy_kwh_source"] = mapped_entity
            daily["solar_true_pv_daily_authority"] = True
            daily["solar_energy_raw_ac_kwh"] = round(max(float(mapped_value), 0.0), 4)
            daily["hybrid_inverter_correction_active"] = bool(hybrid_devices)
            daily["hybrid_true_pv_configured"] = True
            daily["dedicated_true_pv_configured"] = True
            daily["dedicated_pv_entity"] = dedicated_pv_entity
        elif dedicated_pv_entity:
            raw_mapped_solar = float(daily.get("solar_energy_kwh", 0.0) or 0.0)
            true_pv = float(daily.get("solar_true_pv_integrated_kwh", 0.0) or 0.0)
            daily["solar_energy_raw_ac_kwh"] = round(raw_mapped_solar, 4)
            canonical_solar = max(0.0, true_pv)

            # When the canonical Solar Input is introduced or changed part-way
            # through the local day, the live integrator only covers the time
            # since that accounting boundary. Do not publish a partial-day PV
            # total as if it represented the whole calendar day. If all site
            # boundary energy meters are available, reconstruct the missing
            # part from conservation of energy:
            #   PV = Home + Export + Battery charge - Import - Battery discharge
            # This backfill is used only for the transition day. From the next
            # local midnight the canonical Solar Input integration owns the full
            # day again.
            source_changed_today = bool(daily.get("solar_input_source_changed_today"))
            boundary_fields = (
                "house_energy_kwh", "grid_export_energy_kwh",
                "battery_charge_energy_kwh", "grid_import_energy_kwh",
                "battery_discharge_energy_kwh",
            )
            mappings = dict(self.energy_mapping.mappings or {})
            other_generation_configured = any(
                str(mappings.get(field) or "").strip()
                for field in ("wind_power", "generator_power")
            )
            boundary_solar = None
            if not other_generation_configured and all(daily.get(key) is not None for key in boundary_fields):
                boundary_solar = max(0.0, (
                    float(daily.get("house_energy_kwh", 0.0) or 0.0)
                    + float(daily.get("grid_export_energy_kwh", 0.0) or 0.0)
                    + float(daily.get("battery_charge_energy_kwh", 0.0) or 0.0)
                    - float(daily.get("grid_import_energy_kwh", 0.0) or 0.0)
                    - float(daily.get("battery_discharge_energy_kwh", 0.0) or 0.0)
                ))
                daily["solar_energy_boundary_reference_kwh"] = round(boundary_solar, 4)

            # True-PV power remains the canonical source. Its live trapezoid
            # integrator deliberately skips stale gaps >5 minutes; that protects
            # against invented energy, but can leave Today's PV materially short
            # after HA restarts or missed refreshes. On a solar-only generation
            # site the measured Home/Grid/Battery boundary provides a conservative
            # conservation-of-energy floor. Recover only a material deficit so
            # normal meter timestamp skew cannot make the value chatter.
            if boundary_solar is not None:
                deficit = boundary_solar - canonical_solar
                tolerance = max(0.50, boundary_solar * 0.02)
                if deficit > tolerance:
                    canonical_solar = boundary_solar
                    daily["solar_energy_boundary_backfill_kwh"] = round(boundary_solar, 4)
                    daily["solar_energy_boundary_recovered_kwh"] = round(deficit, 4)
                    daily["solar_energy_boundary_recovery_reason"] = (
                        "source_change" if source_changed_today else "missed_true_pv_integration_intervals"
                    )
                    daily["solar_energy_kwh_method"] = "inputs_solar_power_integration_with_boundary_recovery"
                    daily["solar_energy_kwh_source"] = dedicated_pv_entity
                    # Keep the persisted integrator caught up with the recovered
                    # canonical Today total, otherwise every refresh would report
                    # the same missing interval again.
                    daily["solar_true_pv_integrated_kwh"] = round(boundary_solar, 6)
            elif other_generation_configured:
                daily["solar_energy_boundary_recovery_suppressed"] = "other_generation_configured"

            daily["solar_energy_kwh"] = round(max(0.0, canonical_solar), 4)
            daily["solar_hybrid_correction_kwh"] = round(max(0.0, raw_mapped_solar - true_pv), 4)
            if "solar_energy_kwh_method" not in daily or not str(daily.get("solar_energy_kwh_method") or "").startswith("inputs_solar_boundary_backfill"):
                daily["solar_energy_kwh_method"] = "inputs_solar_power_integration"
                daily["solar_energy_kwh_source"] = dedicated_pv_entity
            daily["hybrid_inverter_correction_active"] = bool(hybrid_devices)
            daily["hybrid_true_pv_configured"] = True
            daily["dedicated_true_pv_configured"] = True
            daily["dedicated_pv_entity"] = dedicated_pv_entity
        daily["energy_mapping_v2"] = True

        if isinstance(compact_flows.get("solar_power_w"), (int, float)):
            daily["peak_solar_power_w"] = max(daily.get("peak_solar_power_w", 0), compact_flows["solar_power_w"])
        if isinstance(compact_flows.get("house_power_w"), (int, float)):
            daily["peak_house_power_w"] = max(daily.get("peak_house_power_w", 0), compact_flows["house_power_w"])
        # Per-device energy and runtime integration (v7.1.2).
        device_days = self.data.setdefault("device_daily_summaries", {})
        device_day = device_days.setdefault(day, {})
        for device in snap.get("devices", []):
            device_id = str(device.get("id") or "unknown")
            power_w = device.get("power_w")
            row = device_day.setdefault(device_id, {
                "id": device_id, "name": device.get("name"), "type": device.get("type", "custom"),
                "energy_kwh": 0.0, "runtime_minutes": 0.0, "peak_power_w": 0.0, "sample_count": 0,
            })
            if isinstance(power_w, (int, float)):
                safe_power = max(float(power_w), 0.0)
                row["integrated_energy_kwh"] = round(float(row.get("integrated_energy_kwh", row.get("energy_kwh", 0)) or 0) + safe_power * hours / 1000.0, 5)
                if safe_power > 10.0:
                    row["runtime_minutes"] = round(float(row.get("runtime_minutes", 0) or 0) + interval_seconds / 60.0, 2)
                row["peak_power_w"] = max(float(row.get("peak_power_w", 0) or 0), safe_power)
                row["sample_count"] = int(row.get("sample_count", 0) or 0) + 1
            energy_value = device.get("energy_value_kwh")
            energy_type = str(device.get("energy_type") or "auto")
            if isinstance(energy_value, (int, float)):
                if energy_type == "daily":
                    row["energy_kwh"] = round(max(float(energy_value), 0.0), 5)
                    row["energy_method"] = "measured_daily_energy"
                else:
                    # First reading of each day becomes the persisted baseline. A meter reset
                    # is handled safely by restarting the baseline at the lower reading.
                    baseline = row.get("total_energy_baseline_kwh")
                    if baseline is None or float(energy_value) < float(baseline):
                        baseline = float(energy_value)
                        row["total_energy_baseline_kwh"] = baseline
                    row["total_energy_latest_kwh"] = float(energy_value)
                    row["energy_kwh"] = round(max(float(energy_value) - float(baseline), 0.0), 5)
                    row["energy_method"] = "measured_total_increasing_delta"
            else:
                row["energy_kwh"] = row.get("integrated_energy_kwh", row.get("energy_kwh", 0.0))
                row["energy_method"] = "power_integration"
        for old_day in sorted(device_days)[:-400]:
            device_days.pop(old_day, None)
        # Keep compact daily summaries long enough for year comparisons while
        # retaining minute snapshots for 31 days only.
        daily_keys = sorted(self.data["daily_summaries"])
        for old_day in daily_keys[:-400]:
            self.data["daily_summaries"].pop(old_day, None)
        await self.async_save()
        self.refresh_summary()
        self.event_bus.publish("DataLakeSnapshotCaptured", "DataLake", {"snapshot_count": len(self.data["snapshots"])})
        return snap

    def refresh_summary(self):
        snaps = self.data.get("snapshots", [])
        daily = self.data.get("daily_summaries", {})
        latest = snaps[-1] if snaps else None
        recent_days = sorted(daily.keys())[-7:]
        self.last = {
            "status": "Ready" if snaps else "Waiting",
            "snapshot_count": len(snaps),
            "daily_summary_count": len(daily),
            "device_daily_summary_count": len(self.data.get("device_daily_summaries", {})),
            "latest_snapshot": latest,
            "latest_quality_score": latest.get("quality_score") if latest else None,
            "recent_daily_summaries": {d: daily[d] for d in recent_days},
            "snapshot_retention": self.data.get("metadata", {}).get("snapshot_retention", "31_days"),
            "daily_summary_retention": self.data.get("metadata", {}).get("daily_summary_retention", "400_days"),
            "auto_capture": self.data.get("metadata", {}).get("auto_capture"),
            "safety": "Historical analysis only.",
        }
        return self.last

    def summary(self):
        return self.last


class SimpleSummaryEngine:
    """Generic compact engine retained for the Learning preview."""

    def __init__(self, name: str, event_bus, registry=None, data_bus=None, data_lake=None) -> None:
        self.name = name
        self.event_bus = event_bus
        self.registry = registry
        self.data_bus = data_bus
        self.data_lake = data_lake
        self.last = {"status": "Not ready", "summary": f"{name} not refreshed."}

    def refresh(self):
        device_count = self.registry.summary().get("device_count", 0) if self.registry else 0
        quality = self.data_bus.summary().get("quality_score", 0) if self.data_bus else 0
        snapshot_count = self.data_lake.summary().get("snapshot_count", 0) if self.data_lake else 0
        self.last = {
            "status": "Ready",
            "summary": f"{self.name} observes {device_count} device(s), quality {quality}/100 and {snapshot_count} historical sample(s).",
            "confidence": min(100, quality),
            "patterns": [],
            "safety": "Learning preview only. No device control.",
        }
        return self.last

    def summary(self):
        return self.last


class KnowledgeEngine:
    def __init__(self, event_bus, data_lake) -> None:
        self.event_bus = event_bus
        self.data_lake = data_lake
        self.last = {"status": "Not ready", "insight_count": 0}

    def refresh(self):
        dl = self.data_lake.summary()
        insights = []
        if dl.get("snapshot_count", 0):
            insights.append({"title": "Latest quality", "detail": f"Latest quality score is {dl.get('latest_quality_score')}/100."})
        else:
            insights.append({"title": "Waiting for data", "detail": "No Data Lake snapshots yet."})
        self.last = {
            "status": "Ready",
            "insight_count": len(insights),
            "insights": insights,
            "data_readiness": "Good" if dl.get("snapshot_count", 0) >= 288 else "Medium" if dl.get("snapshot_count", 0) >= 12 else "Low",
            "snapshot_count": dl.get("snapshot_count", 0),
            "summary": f"Generated {len(insights)} insight(s).",
            "safety": "Historical insight only.",
        }
        self.event_bus.publish("KnowledgeUpdated", "KnowledgeEngine", {"insight_count": len(insights)})
        return self.last

    def summary(self):
        return self.last


class BriefingCenter:
    def __init__(self, event_bus, registry, diagnostics, knowledge) -> None:
        self.event_bus = event_bus
        self.registry = registry
        self.diagnostics = diagnostics
        self.knowledge = knowledge
        self.last = {"status": "Not ready"}

    def refresh(self):
        reg = self.registry.summary()
        diag = self.diagnostics.summary()
        know = self.knowledge.summary()
        self.last = {
            "status": "Ready",
            "morning": f"Good morning. Registry has {reg.get('device_count', 0)} device(s). Diagnostics: {diag.get('status')}.",
            "evening": f"Evening report. Knowledge readiness: {know.get('data_readiness', 'Unknown')}.",
            "system": f"System diagnostics: {diag.get('error_count', 0)} errors, {diag.get('warning_count', 0)} warnings.",
            "data": know.get("summary"),
            "migration": "Migration tools are available.",
            "briefing": "AION EMS briefing generated.",
            "safety": "Briefing only.",
        }
        return self.last

    def summary(self):
        return self.last


class QuestionLibrary:
    def __init__(self, event_bus, registry, diagnostics, data_lake, knowledge, briefing) -> None:
        self.event_bus = event_bus
        self.registry = registry
        self.diagnostics = diagnostics
        self.data_lake = data_lake
        self.knowledge = knowledge
        self.briefing = briefing
        self.last = {"status": "Not ready"}

    def refresh(self):
        answers = [
            {"question": "What is my latest system status?", "answer": self.briefing.summary().get("system")},
            {"question": "Is my data good enough?", "answer": f"Data readiness is {self.knowledge.summary().get('data_readiness')}."},
            {"question": "What should I look at today?", "answer": self.briefing.summary().get("morning")},
        ]
        self.last = {"status": "Ready", "question_count": len(answers), "answers": answers, "questions": [a["question"] for a in answers], "summary": f"{len(answers)} answers ready.", "safety": "Rule-based answers only."}
        return self.last

    def summary(self):
        return self.last


class DiagnosticsEngine:
    def __init__(self, event_bus, registry) -> None:
        self.event_bus = event_bus
        self.registry = registry
        self.last = {"status": "Healthy", "error_count": 0, "warning_count": 0, "issues": [], "message": "Diagnostics healthy."}

    def refresh(self):
        warnings = []
        if self.registry.summary().get("device_count", 0) == 0:
            warnings.append({"severity": "warning", "code": "NO_DEVICES", "message": "No devices registered yet."})
        self.last = {"status": "Warning" if warnings else "Healthy", "error_count": 0, "warning_count": len(warnings), "issues": warnings, "message": "Review diagnostics." if warnings else "Diagnostics healthy."}
        return self.last

    def summary(self):
        return self.last
