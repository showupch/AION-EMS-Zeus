"""AION EMS services."""

from __future__ import annotations

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    DOMAIN,
    SERVICE_EXPORT_REGISTRY,
    SERVICE_BACKUP_REGISTRY,
    SERVICE_RESTORE_LATEST_BACKUP,
    SERVICE_RELOAD_REGISTRY,
    SERVICE_REFRESH_ENTITY_DISCOVERY,
    SERVICE_REFRESH_ENERGY_MAPPING,
    SERVICE_REFRESH_ENERGY_FLOW,
    SERVICE_REFRESH_INTEGRATION_HUB,
    SERVICE_REFRESH_DATA_BUS,
    SERVICE_CAPTURE_DATA_LAKE_SNAPSHOT,
    SERVICE_REFRESH_DATA_LAKE_SUMMARY,
    SERVICE_REFRESH_KNOWLEDGE_ENGINE,
    SERVICE_REFRESH_BRIEFING_CENTER,
    SERVICE_REFRESH_QUESTION_LIBRARY,
    SERVICE_REFRESH_CAPABILITY_REPORT,
    SERVICE_IMPORT_DISCOVERY_CANDIDATE,
    SERVICE_IMPORT_RECOMMENDED_DEVICES,
    SERVICE_REFRESH_DEVICE_IMPORT_REVIEW,
    SERVICE_IMPORT_REVIEWED_DEVICES,
    SERVICE_DEVICE_MANAGER_BUILD_REVIEW,
    SERVICE_DEVICE_MANAGER_IMPORT_READY,
    SERVICE_DEVICE_MANAGER_REMOVE_DEVICE,
    SERVICE_REMOVE_AUTO_IMPORTED_DEVICES,
    SERVICE_HELIOS_MIGRATION_ANALYZE,
    SERVICE_HELIOS_MIGRATION_PREVIEW,
    SERVICE_HELIOS_SMART_IMPORT_REPORT,
    SERVICE_APPLY_HELIOS_MIGRATION_PREVIEW,
    SERVICE_ADD_DEVICE,
    SERVICE_UPDATE_DEVICE,
    SERVICE_REMOVE_DEVICE,
    SERVICE_ADD_ROOM,
    SERVICE_UPDATE_ROOM,
    SERVICE_REMOVE_ROOM,
    SERVICE_ADD_GROUP,
    SERVICE_UPDATE_GROUP,
    SERVICE_REMOVE_GROUP,
    SERVICE_ADD_DEVICE_PREVIEW,
    SERVICE_REFRESH_FORECAST,
    SERVICE_REFRESH_OPTIMIZER_PREVIEW,
    SERVICE_REFRESH_SCHEDULER_PREVIEW,
    SERVICE_REFRESH_LEARNING_PREVIEW,
    SERVICE_LIFECYCLE_STATUS,
    SERVICE_TEST_ENTITY_MAPPING,
    SERVICE_SAVE_ENTITY_MAPPING,
    SERVICE_CLEAR_ENTITY_MAPPING,
    SERVICE_SAVE_WEATHER_SOURCE,
    SERVICE_CLEAR_WEATHER_SOURCE,
    SERVICE_SAVE_TARIFF_SETTINGS,
    SERVICE_CLEAR_TARIFF_SETTINGS,
    SERVICE_SAVE_BATTERY_CAPACITY,
    SERVICE_SAVE_HOME_PROFILE,
    SERVICE_CLEAR_BATTERY_CAPACITY,
    SERVICE_SET_DATA_EPOCH,
    SERVICE_CLEAR_DATA_EPOCH,
    SERVICE_SAVE_NOTIFICATION_SETTINGS,
    SERVICE_TEST_NOTIFICATION,
    SERVICE_SAVE_PLUGIN_SETTINGS,
    SERVICE_TEST_PLUGIN,
    SERVICE_CREATE_NAS_BACKUP,
    SERVICE_REFRESH_PLUGIN_DISCOVERY,
    SERVICE_RUN_QA_HEALTH_CHECK,
    SERVICE_REGISTER_BATTERY_PROFILE,
    SERVICE_CLEAR_BATTERY_PROFILE,
    SERVICE_PREVIEW_HA_ENERGY_IMPORT,
    SERVICE_APPLY_HA_ENERGY_IMPORT,
)


REGISTERED = False


def _core(hass: HomeAssistant):
    return hass.data[DOMAIN]["core"]


async def _refresh_aion_entities(hass: HomeAssistant) -> None:
    """Force Home Assistant to refresh AION EMS sensors after service actions."""
    await hass.services.async_call(
        "homeassistant",
        "update_entity",
        {
            "entity_id": [
                "sensor.aion_ems_zeus_device_import_wizard",
                "sensor.aion_ems_zeus_device_manager",
                "sensor.aion_ems_zeus_registry_summary",
                "sensor.aion_ems_zeus_energy_mapping",
                "sensor.aion_ems_zeus_energy_flow",
                "sensor.aion_ems_zeus_diagnostics",
                "sensor.aion_ems_zeus_capability_report",
                "sensor.aion_ems_zeus_update_engine",
                "sensor.aion_ems_zeus_historical_analytics",
                "sensor.aion_ems_zeus_forecast",
                "sensor.aion_ems_zeus_optimizer_preview",
                "sensor.aion_ems_zeus_scheduler_preview",
                "sensor.aion_ems_zeus_qa_diagnostics",
            ]
        },
        blocking=True,
    )


def _device_schema(require_all=True):
    req = vol.Required if require_all else vol.Optional
    return vol.Schema({
        req("device_id"): cv.string,
        req("name"): cv.string,
        req("power_entity"): cv.entity_id,
        req("energy_entity"): cv.entity_id,
        vol.Optional("energy_type", default="auto"): vol.In(["auto", "daily", "total_increasing"]),
        vol.Optional("state_entity"): cv.entity_id,
        vol.Optional("availability_entity"): cv.entity_id,
        vol.Optional("device_type", default="custom"): cv.string,
        vol.Optional("category", default="other"): cv.string,
        vol.Optional("room_id", default="unassigned"): cv.string,
        vol.Optional("group_ids", default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("priority", default="medium"): cv.string,
        vol.Optional("icon", default="mdi:power-plug"): cv.icon,
        vol.Optional("enabled", default=True): cv.boolean,
        vol.Optional("notes", default=""): cv.string,
        vol.Optional("hybrid_inverter", default=False): cv.boolean,
        vol.Optional("solar_power_entity"): cv.entity_id,
        vol.Optional("temperature_entity"): cv.entity_id,
        vol.Optional("cop_entity"): cv.entity_id,
        vol.Optional("thermal_power_entity"): cv.entity_id,
        vol.Optional("thermal_energy_entity"): cv.entity_id,
        vol.Optional("supply_temperature_entity"): cv.entity_id,
        vol.Optional("return_temperature_entity"): cv.entity_id,
        vol.Optional("outdoor_temperature_entity"): cv.entity_id,
        vol.Optional("compressor_state_entity"): cv.entity_id,
        vol.Optional("compressor_runtime_entity"): cv.entity_id,
        vol.Optional("compressor_starts_entity"): cv.entity_id,
        vol.Optional("dhw_temperature_entity"): cv.entity_id,
        vol.Optional("dhw_energy_entity"): cv.entity_id,
        vol.Optional("heating_energy_entity"): cv.entity_id,
        vol.Optional("cooling_energy_entity"): cv.entity_id,
        vol.Optional("operating_mode_entity"): cv.entity_id,
        vol.Optional("target_temperature_entity"): cv.entity_id,
        vol.Optional("jaz_entity"): cv.entity_id,
        vol.Optional("heat_carrier_forward_entity"): cv.entity_id,
        vol.Optional("heat_carrier_return_entity"): cv.entity_id,
        vol.Optional("source_in_temperature_entity"): cv.entity_id,
        vol.Optional("source_out_temperature_entity"): cv.entity_id,
        vol.Optional("source_pump_speed_entity"): cv.entity_id,
        vol.Optional("compressor_activity_entity"): cv.entity_id,
        vol.Optional("compressor_speed_entity"): cv.entity_id,
        vol.Optional("compressor_target_speed_entity"): cv.entity_id,
        vol.Optional("dhw_target_temperature_entity"): cv.entity_id,
    })


def _device_update_schema():
    """Device update schema without injected defaults.

    Defaults are correct for Add Device, but unsafe for Update Device because
    they can overwrite persisted metadata/mappings when an older frontend omits
    a field. The update handler merges only fields actually supplied.
    """
    return vol.Schema({
        vol.Required("device_id"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("power_entity"): cv.entity_id,
        vol.Optional("energy_entity"): cv.entity_id,
        vol.Optional("energy_type"): vol.In(["auto", "daily", "total_increasing"]),
        vol.Optional("state_entity"): cv.entity_id,
        vol.Optional("availability_entity"): cv.entity_id,
        vol.Optional("device_type"): cv.string,
        vol.Optional("category"): cv.string,
        vol.Optional("room_id"): cv.string,
        vol.Optional("group_ids"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("priority"): cv.string,
        vol.Optional("icon"): cv.icon,
        vol.Optional("enabled"): cv.boolean,
        vol.Optional("notes"): cv.string,
        vol.Optional("hybrid_inverter"): cv.boolean,
        vol.Optional("solar_power_entity"): cv.entity_id,
        vol.Optional("temperature_entity"): cv.entity_id,
        vol.Optional("cop_entity"): cv.entity_id,
        vol.Optional("thermal_power_entity"): cv.entity_id,
        vol.Optional("thermal_energy_entity"): cv.entity_id,
        vol.Optional("supply_temperature_entity"): cv.entity_id,
        vol.Optional("return_temperature_entity"): cv.entity_id,
        vol.Optional("outdoor_temperature_entity"): cv.entity_id,
        vol.Optional("compressor_state_entity"): cv.entity_id,
        vol.Optional("compressor_runtime_entity"): cv.entity_id,
        vol.Optional("compressor_starts_entity"): cv.entity_id,
        vol.Optional("dhw_temperature_entity"): cv.entity_id,
        vol.Optional("dhw_energy_entity"): cv.entity_id,
        vol.Optional("heating_energy_entity"): cv.entity_id,
        vol.Optional("cooling_energy_entity"): cv.entity_id,
        vol.Optional("operating_mode_entity"): cv.entity_id,
        vol.Optional("target_temperature_entity"): cv.entity_id,
        vol.Optional("jaz_entity"): cv.entity_id,
        vol.Optional("heat_carrier_forward_entity"): cv.entity_id,
        vol.Optional("heat_carrier_return_entity"): cv.entity_id,
        vol.Optional("source_in_temperature_entity"): cv.entity_id,
        vol.Optional("source_out_temperature_entity"): cv.entity_id,
        vol.Optional("source_pump_speed_entity"): cv.entity_id,
        vol.Optional("compressor_activity_entity"): cv.entity_id,
        vol.Optional("compressor_speed_entity"): cv.entity_id,
        vol.Optional("compressor_target_speed_entity"): cv.entity_id,
        vol.Optional("dhw_target_temperature_entity"): cv.entity_id,
    })


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register services once."""
    global REGISTERED
    if REGISTERED and hass.services.has_service(DOMAIN, SERVICE_SAVE_TARIFF_SETTINGS) and hass.services.has_service(DOMAIN, SERVICE_CLEAR_TARIFF_SETTINGS):
        return
    # Do not mark setup complete until every service has been registered.
    # This allows recovery from a partial setup after an upgrade or reload.
    REGISTERED = False


    async def save_notification_settings(call: ServiceCall) -> None:
        core=_core(hass); current=core.registry.data.setdefault("notification_settings", {})
        current.update({k:v for k,v in call.data.items() if k!="mobile_targets"})
        if "mobile_targets" in call.data: current["mobile_targets"]=list(call.data["mobile_targets"])
        categories=current.setdefault("categories", {})
        for key in ("recommendation","battery","scheduler","high_grid_import","solar_surplus","tariff","daily_report","system_health"):
            field=f"category_{key}"
            if field in call.data: categories[key]=call.data[field]
        await core.registry.async_save(); core.notifications.refresh(); await _refresh_aion_entities(hass)

    async def test_notification(call: ServiceCall) -> None:
        core=_core(hass); core.notifications.refresh(); await core.notifications.async_test(hass); await _refresh_aion_entities(hass)

    async def save_plugin_settings(call: ServiceCall) -> None:
        core=_core(hass); plugin_id=call.data["plugin_id"]
        values={k:v for k,v in call.data.items() if k!="plugin_id"}
        await core.integration_hub.async_save_settings(plugin_id, values)
        await _refresh_aion_entities(hass)

    async def refresh_plugin_discovery(call: ServiceCall) -> None:
        plugin_id = str(call.data.get("plugin_id", "") or "").strip()
        await _core(hass).integration_hub.async_refresh_plugin(plugin_id or None)
        await _refresh_aion_entities(hass)

    async def test_plugin(call: ServiceCall) -> None:
        core=_core(hass); plugin_id=call.data["plugin_id"]
        cfg=core.registry.data.setdefault("plugin_settings",{}).get(plugin_id,{})
        if plugin_id in ("email","pushover"):
            selected=str(cfg.get("service","")).strip()
            if selected.startswith("entity:notify."):
                entity_id=selected.split("entity:",1)[1]
                if not hass.states.get(entity_id): raise ValueError("The selected notify entity is not available")
                await hass.services.async_call("notify","send_message",{"entity_id":entity_id,"title":"AION EMS Zeus test","message":"Zeus v10.18.4 plugin test. Recommendation Only mode is active."},blocking=True)
            else:
                service=selected.replace("notify.","")
                if not service or not hass.services.has_service("notify",service): raise ValueError("Select an available notify service or notify entity first")
                await hass.services.async_call("notify",service,{"title":"AION EMS Zeus test","message":"Zeus v10.18.4 plugin test. Recommendation Only mode is active."},blocking=True)
        elif plugin_id=="nas_backup": await core.integration_hub.async_test_nas()
        else: await core.integration_hub.async_refresh()
        await _refresh_aion_entities(hass)


    async def preview_home_assistant_energy_import(call: ServiceCall) -> None:
        """Explicit read-only scan of Home Assistant Energy preferences."""
        core = _core(hass)
        result = await core.ha_energy_import.async_preview()
        core.device_import_manager.last_validation = result
        await _refresh_aion_entities(hass)

    async def apply_home_assistant_energy_import(call: ServiceCall) -> None:
        """Explicitly apply reviewed Home Assistant Energy sources/devices."""
        core = _core(hass)
        result = await core.ha_energy_import.async_apply(
            import_whole_home=call.data.get("import_whole_home", True),
            import_devices=call.data.get("import_devices", True),
            overwrite_existing=call.data.get("overwrite_existing", False),
        )
        core.device_import_manager.last_validation = result
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)


    async def run_qa_health_check(call: ServiceCall) -> None:
        core = _core(hass)
        core.qa_diagnostics.run()
        await _refresh_aion_entities(hass)

    async def create_nas_backup(call: ServiceCall) -> None:
        await _core(hass).integration_hub.async_create_nas_backup(); await _refresh_aion_entities(hass)

    async def export_registry(call: ServiceCall) -> None:
        _core(hass).event_bus.publish("RegistryExportRequested", "Services", _core(hass).registry.summary())

    async def backup_registry(call: ServiceCall) -> None:
        await _core(hass).registry.async_backup()
        _core(hass).refresh_pipeline()

    async def restore_latest_backup(call: ServiceCall) -> None:
        # Reserved safe no-op for now
        _core(hass).event_bus.publish("RestoreLatestBackupRequested", "Services", {"status": "not_implemented"})

    async def reload_registry(call: ServiceCall) -> None:
        await _core(hass).registry.async_load()
        _core(hass).refresh_pipeline()

    async def refresh_entity_discovery(call: ServiceCall) -> None:
        _core(hass).discovery.refresh()
        _core(hass).energy_mapping.refresh()
        await _core(hass).integration_hub.async_refresh()
        _core(hass).data_bus.refresh()

    async def refresh_energy_mapping(call: ServiceCall) -> None:
        _core(hass).energy_mapping.refresh()
        _core(hass).energy_flow.refresh()
        _core(hass).data_bus.refresh()

    async def refresh_energy_flow(call: ServiceCall) -> None:
        _core(hass).energy_mapping.refresh()
        _core(hass).energy_flow.refresh()

    async def refresh_integration_hub(call: ServiceCall) -> None:
        await _core(hass).integration_hub.async_refresh()
        _core(hass).data_bus.refresh()

    async def refresh_data_bus(call: ServiceCall) -> None:
        _core(hass).data_bus.refresh()

    async def capture_data_lake_snapshot(call: ServiceCall) -> None:
        await _core(hass).async_capture_pipeline_snapshot()

    async def refresh_data_lake_summary(call: ServiceCall) -> None:
        _core(hass).data_lake.refresh_summary()

    async def refresh_knowledge_engine(call: ServiceCall) -> None:
        _core(hass).knowledge.refresh()

    async def refresh_briefing_center(call: ServiceCall) -> None:
        _core(hass).briefing.refresh()

    async def refresh_question_library(call: ServiceCall) -> None:
        _core(hass).question_library.refresh()

    async def refresh_capability_report(call: ServiceCall) -> None:
        _core(hass).capability.refresh()

    async def add_device(call: ServiceCall) -> None:
        core = _core(hass)
        device = core.registry.build_device(
            device_id=call.data["device_id"],
            name=call.data["name"],
            power_entity=call.data["power_entity"],
            enabled=call.data.get("enabled", True),
            device_type=call.data.get("device_type", "custom"),
            category=call.data.get("category", "other"),
            room_id=call.data.get("room_id", "unassigned"),
            group_ids=call.data.get("group_ids", []),
            energy_entity=call.data["energy_entity"],
            energy_type=call.data.get("energy_type", "auto"),
            state_entity=call.data.get("state_entity"),
            availability_entity=call.data.get("availability_entity"),
            priority=call.data.get("priority", "medium"),
            icon=call.data.get("icon", "mdi:power-plug"),
            notes=call.data.get("notes", ""),
            hybrid_inverter=call.data.get("hybrid_inverter", False),
            solar_power_entity=call.data.get("solar_power_entity"),
            temperature_entity=call.data.get("temperature_entity"),
            cop_entity=call.data.get("cop_entity"),
            thermal_power_entity=call.data.get("thermal_power_entity"),
            thermal_energy_entity=call.data.get("thermal_energy_entity"),
            supply_temperature_entity=call.data.get("supply_temperature_entity"),
            return_temperature_entity=call.data.get("return_temperature_entity"),
            outdoor_temperature_entity=call.data.get("outdoor_temperature_entity"),
            compressor_state_entity=call.data.get("compressor_state_entity"),
            compressor_runtime_entity=call.data.get("compressor_runtime_entity"),
            compressor_starts_entity=call.data.get("compressor_starts_entity"),
            dhw_temperature_entity=call.data.get("dhw_temperature_entity"),
            dhw_energy_entity=call.data.get("dhw_energy_entity"),
            heating_energy_entity=call.data.get("heating_energy_entity"),
            cooling_energy_entity=call.data.get("cooling_energy_entity"),
            operating_mode_entity=call.data.get("operating_mode_entity"),
            target_temperature_entity=call.data.get("target_temperature_entity"),
            jaz_entity=call.data.get("jaz_entity"),
            heat_carrier_forward_entity=call.data.get("heat_carrier_forward_entity"),
            heat_carrier_return_entity=call.data.get("heat_carrier_return_entity"),
            source_in_temperature_entity=call.data.get("source_in_temperature_entity"),
            source_out_temperature_entity=call.data.get("source_out_temperature_entity"),
            source_pump_speed_entity=call.data.get("source_pump_speed_entity"),
            compressor_activity_entity=call.data.get("compressor_activity_entity"),
            compressor_speed_entity=call.data.get("compressor_speed_entity"),
            compressor_target_speed_entity=call.data.get("compressor_target_speed_entity"),
            dhw_target_temperature_entity=call.data.get("dhw_target_temperature_entity"),
        )
        issues = await core.registry.async_add_device(device)
        core.device_import_manager.last_validation = {"status": "Imported" if not any(i["severity"] == "error" for i in issues) else "Error", "device": device, "issues": issues, "message": "Device import completed."}
        core.refresh_pipeline()

    async def update_device(call: ServiceCall) -> None:
        """Update a device without dropping fields omitted by older/cached frontends."""
        core = _core(hass)
        device_id = call.data["device_id"]
        existing = next(
            (dict(item) for item in core.registry.data.get("devices", [])
             if str(item.get("id")) == str(device_id)),
            {},
        )
        merged = dict(existing)
        merged.update(dict(call.data))
        # Build through the canonical registry constructor so types and energy
        # classification remain normalized, but preserve every known mapping.
        device = core.registry.build_device(
            device_id=device_id,
            name=merged.get("name") or existing.get("name") or device_id,
            power_entity=merged.get("power_entity") or existing.get("power_entity"),
            energy_entity=merged.get("energy_entity") or existing.get("energy_entity"),
            energy_type=merged.get("energy_type", existing.get("energy_type", "auto")),
            enabled=merged.get("enabled", existing.get("enabled", True)),
            device_type=merged.get("device_type", merged.get("type", existing.get("type", "custom"))),
            category=merged.get("category", existing.get("category", "other")),
            room_id=merged.get("room_id", existing.get("room_id", "unassigned")),
            group_ids=merged.get("group_ids", existing.get("group_ids", [])),
            state_entity=merged.get("state_entity", existing.get("state_entity")),
            availability_entity=merged.get("availability_entity", existing.get("availability_entity")),
            priority=merged.get("priority", existing.get("priority", "medium")),
            icon=merged.get("icon", existing.get("icon", "mdi:power-plug")),
            notes=merged.get("notes", existing.get("notes", "")),
            hybrid_inverter=merged.get("hybrid_inverter", existing.get("hybrid_inverter", False)),
            solar_power_entity=merged.get("solar_power_entity", existing.get("solar_power_entity")),
            temperature_entity=merged.get("temperature_entity", existing.get("temperature_entity")),
            cop_entity=merged.get("cop_entity", existing.get("cop_entity")),
            thermal_power_entity=merged.get("thermal_power_entity", existing.get("thermal_power_entity")),
            thermal_energy_entity=merged.get("thermal_energy_entity", existing.get("thermal_energy_entity")),
            supply_temperature_entity=merged.get("supply_temperature_entity", existing.get("supply_temperature_entity")),
            return_temperature_entity=merged.get("return_temperature_entity", existing.get("return_temperature_entity")),
            outdoor_temperature_entity=merged.get("outdoor_temperature_entity", existing.get("outdoor_temperature_entity")),
            compressor_state_entity=merged.get("compressor_state_entity", existing.get("compressor_state_entity")),
            compressor_runtime_entity=merged.get("compressor_runtime_entity", existing.get("compressor_runtime_entity")),
            compressor_starts_entity=merged.get("compressor_starts_entity", existing.get("compressor_starts_entity")),
            dhw_temperature_entity=merged.get("dhw_temperature_entity", existing.get("dhw_temperature_entity")),
            dhw_energy_entity=merged.get("dhw_energy_entity", existing.get("dhw_energy_entity")),
            heating_energy_entity=merged.get("heating_energy_entity", existing.get("heating_energy_entity")),
            cooling_energy_entity=merged.get("cooling_energy_entity", existing.get("cooling_energy_entity")),
            operating_mode_entity=merged.get("operating_mode_entity", existing.get("operating_mode_entity")),
            target_temperature_entity=merged.get("target_temperature_entity", existing.get("target_temperature_entity")),
            jaz_entity=merged.get("jaz_entity", existing.get("jaz_entity")),
            heat_carrier_forward_entity=merged.get("heat_carrier_forward_entity", existing.get("heat_carrier_forward_entity")),
            heat_carrier_return_entity=merged.get("heat_carrier_return_entity", existing.get("heat_carrier_return_entity")),
            source_in_temperature_entity=merged.get("source_in_temperature_entity", existing.get("source_in_temperature_entity")),
            source_out_temperature_entity=merged.get("source_out_temperature_entity", existing.get("source_out_temperature_entity")),
            source_pump_speed_entity=merged.get("source_pump_speed_entity", existing.get("source_pump_speed_entity")),
            compressor_activity_entity=merged.get("compressor_activity_entity", existing.get("compressor_activity_entity")),
            compressor_speed_entity=merged.get("compressor_speed_entity", existing.get("compressor_speed_entity")),
            compressor_target_speed_entity=merged.get("compressor_target_speed_entity", existing.get("compressor_target_speed_entity")),
            dhw_target_temperature_entity=merged.get("dhw_target_temperature_entity", existing.get("dhw_target_temperature_entity")),
        )
        issues = await core.registry.async_add_device(device)
        core.device_import_manager.last_validation = {
            "status": "Updated" if not any(i["severity"] == "error" for i in issues) else "Error",
            "device": device,
            "issues": issues,
            "message": "Device update completed with persistence-safe field merge.",
        }
        core.refresh_pipeline()

    async def remove_device(call: ServiceCall) -> None:
        core = _core(hass)
        await core.registry.async_remove_device(call.data["device_id"])
        core.refresh_pipeline()

    async def import_discovery_candidate(call: ServiceCall) -> None:
        await add_device(call)

    async def import_recommended_devices(call: ServiceCall) -> None:
        core = _core(hass)
        core.discovery.refresh()
        dry_run = call.data.get("dry_run", True)
        include_system = call.data.get("include_system_devices", True)
        include_appliances = call.data.get("include_appliances", True)
        overwrite = call.data.get("overwrite_existing", False)

        categories = []
        if include_system:
            categories += ["solar", "battery", "grid", "ev", "heat_pump", "water_heater"]
        if include_appliances:
            categories += ["dishwasher", "washing_machine", "dryer"]

        type_map = {"solar": "solar_inverter", "battery": "battery", "grid": "custom", "ev": "ev_charger", "heat_pump": "heat_pump", "water_heater": "water_heater", "dishwasher": "dishwasher", "washing_machine": "washing_machine", "dryer": "dryer"}

        existing = {d.get("id") for d in core.registry.data.get("devices", [])}
        imported = []
        skipped = []

        for cat in categories:
            candidates = core.discovery.summary().get(f"{cat}_candidates", [])
            if not candidates:
                skipped.append({"category": cat, "reason": "no_candidate_found"})
                continue
            candidate = candidates[0]
            device_type = type_map.get(cat, "custom")
            defaults = core.device_import_manager.defaults_for(device_type)
            device_id = f"auto_{cat}"
            if device_id in existing and not overwrite:
                skipped.append({"category": cat, "device_id": device_id, "reason": "already_exists"})
                continue
            device = core.registry.build_device(
                device_id=device_id,
                name=candidate.get("name") or cat.replace("_", " ").title(),
                power_entity=candidate["entity_id"],
                device_type=device_type,
                category=defaults["category"],
                room_id=defaults["room_id"],
                group_ids=defaults["group_ids"],
                priority=defaults["priority"],
                icon=defaults["icon"],
                notes=f"Recommended import from {cat}.",
            )
            imported.append({"category": cat, "device_id": device_id, "name": device["name"], "entity_id": device["power_entity"], "dry_run": dry_run})
            if not dry_run:
                await core.registry.async_add_device(device)
                existing.add(device_id)

        core.device_import_manager.last_validation = {"status": "Dry Run" if dry_run else "Imported", "message": f"Recommended import finished. Imported candidates: {len(imported)}. Skipped: {len(skipped)}. Dry run: {dry_run}.", "imported": imported, "skipped": skipped, "issues": []}
        core.refresh_pipeline()

    async def remove_auto_imported_devices(call: ServiceCall) -> None:
        core = _core(hass)
        dry_run = call.data.get("dry_run", True)
        removed = await core.registry.async_remove_auto_devices(dry_run=dry_run)
        core.device_import_manager.last_validation = {"status": "Dry Run" if dry_run else "Removed", "message": f"Auto cleanup found {len(removed)} device(s). Dry run: {dry_run}.", "removed": removed, "issues": []}
        core.refresh_pipeline()

    async def add_room(call: ServiceCall) -> None:
        core = _core(hass)
        room = {"id": call.data["room_id"], "name": call.data["name"], "icon": call.data.get("icon", "mdi:home"), "notes": call.data.get("notes", "")}
        core.registry.data["rooms"] = [r for r in core.registry.data.get("rooms", []) if r.get("id") != room["id"]] + [room]
        await core.registry.async_save()
        core.refresh_pipeline()

    async def update_room(call: ServiceCall) -> None:
        await add_room(call)

    async def remove_room(call: ServiceCall) -> None:
        core = _core(hass)
        core.registry.data["rooms"] = [r for r in core.registry.data.get("rooms", []) if r.get("id") != call.data["room_id"]]
        await core.registry.async_save()
        core.refresh_pipeline()

    async def add_group(call: ServiceCall) -> None:
        core = _core(hass)
        group = {"id": call.data["group_id"], "name": call.data["name"], "category": call.data.get("category", "other"), "priority": call.data.get("priority", "medium"), "icon": call.data.get("icon", "mdi:group"), "notes": call.data.get("notes", "")}
        core.registry.data["groups"] = [g for g in core.registry.data.get("groups", []) if g.get("id") != group["id"]] + [group]
        await core.registry.async_save()
        core.refresh_pipeline()

    async def update_group(call: ServiceCall) -> None:
        await add_group(call)

    async def remove_group(call: ServiceCall) -> None:
        core = _core(hass)
        core.registry.data["groups"] = [g for g in core.registry.data.get("groups", []) if g.get("id") != call.data["group_id"]]
        await core.registry.async_save()
        core.refresh_pipeline()

    async def helios_migration_analyze(call: ServiceCall) -> None:
        _core(hass).migration.analyze()

    async def helios_migration_preview(call: ServiceCall) -> None:
        _core(hass).migration.build_preview()

    async def helios_smart_import_report(call: ServiceCall) -> None:
        _core(hass).migration.smart_report()

    async def apply_helios_migration_preview(call: ServiceCall) -> None:
        await _core(hass).migration.async_apply_preview()

    async def refresh_preview(call: ServiceCall) -> None:
        _core(hass).refresh_pipeline()


    async def refresh_device_import_review(call: ServiceCall) -> None:
        core = _core(hass)
        core.event_bus.publish("DeviceManagerBuildReviewServiceCalled", "Services", {})
        core.device_import_wizard.build_review(
            include_system_devices=call.data.get("include_system_devices", True),
            include_appliances=call.data.get("include_appliances", True),
            overwrite_existing=call.data.get("overwrite_existing", False),
        )
        core.device_import_manager.last_validation = core.device_import_wizard.summary()
        core.capability.refresh()

    async def import_reviewed_devices(call: ServiceCall) -> None:
        core = _core(hass)
        result = await core.device_import_wizard.async_import_reviewed(
            dry_run=call.data.get("dry_run", True),
            include_system_devices=call.data.get("include_system_devices", True),
            include_appliances=call.data.get("include_appliances", True),
            overwrite_existing=call.data.get("overwrite_existing", False),
        )
        core.device_import_manager.last_validation = result
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)


    async def device_manager_build_review(call: ServiceCall) -> None:
        """Device Manager UI: build import review."""
        core = _core(hass)
        core.device_import_wizard.build_review(
            include_system_devices=call.data.get("include_system_devices", True),
            include_appliances=call.data.get("include_appliances", False),
            overwrite_existing=call.data.get("overwrite_existing", False),
        )
        core.device_import_manager.last_validation = core.device_import_wizard.summary()
        core.capability.refresh()
        await _refresh_aion_entities(hass)

    async def device_manager_import_ready(call: ServiceCall) -> None:
        """Device Manager UI: import ready reviewed devices."""
        core = _core(hass)
        result = await core.device_import_wizard.async_import_reviewed(
            dry_run=call.data.get("dry_run", True),
            include_system_devices=call.data.get("include_system_devices", True),
            include_appliances=call.data.get("include_appliances", False),
            overwrite_existing=call.data.get("overwrite_existing", False),
        )
        core.device_import_manager.last_validation = result
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)

    async def device_manager_remove_device(call: ServiceCall) -> None:
        """Device Manager UI: remove one registry device."""
        core = _core(hass)
        await core.registry.async_remove_device(call.data["device_id"])
        core.device_import_manager.last_validation = {
            "status": "Removed",
            "message": f"Removed device {call.data['device_id']}.",
            "issues": [],
        }
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)

    async def test_entity_mapping(call: ServiceCall) -> None:
        core = _core(hass)
        result = core.energy_mapping.validate(call.data["field"], call.data["entity_id"])
        if call.data["field"] in {"grid_power", "grid_import_power", "grid_export_power"}:
            result["grid_mode"] = call.data.get("grid_mode", "bidirectional" if call.data["field"] == "grid_power" else "separate")
        if call.data["field"] == "grid_power":
            result["sign_convention"] = call.data.get("sign_convention", "positive_import")
        if call.data["field"] in {"battery_power", "battery_charge_power", "battery_discharge_power"}:
            result["battery_mode"] = call.data.get("battery_mode", "bidirectional" if call.data["field"] == "battery_power" else "separate")
        if call.data["field"] == "battery_power":
            result["battery_sign_convention"] = call.data.get("battery_sign_convention", "positive_discharge")
        result["suggestions"] = core.energy_mapping.suggestions(call.data["field"])
        core.energy_mapping.last_test = result
        core.energy_mapping.refresh()
        core.device_import_manager.last_validation = {"status": "Test Passed" if result["status"] == "valid" else "Test Failed", "message": f"Mapping test for {call.data['field']}.", "issues": result["issues"], "mapping_test": result}
        await _refresh_aion_entities(hass)


    async def save_weather_source(call: ServiceCall) -> None:
        core = _core(hass)
        entity_id = call.data["entity_id"]
        state = hass.states.get(entity_id)
        if state is None or not entity_id.startswith("weather."):
            raise vol.Invalid("A valid Home Assistant weather.* entity is required")
        core.registry.data.setdefault("sources", {})["weather"] = {
            "entity_id": entity_id,
            "enabled": True,
            "saved_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        core.registry.data.setdefault("audit", []).append({"action": "save_weather_source", "entity_id": entity_id})
        await core.registry.async_save()
        core.weather.refresh()
        if hasattr(core.weather, "async_refresh_forecast"):
            await core.weather.async_refresh_forecast()
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)

    async def clear_weather_source(call: ServiceCall) -> None:
        core = _core(hass)
        core.registry.data.setdefault("sources", {})["weather"] = {"entity_id": None, "enabled": False}
        core.registry.data.setdefault("audit", []).append({"action": "clear_weather_source"})
        await core.registry.async_save()
        core.weather.refresh()
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)


    async def save_tariff_settings(call: ServiceCall) -> None:
        core = _core(hass)
        import_tariff = float(call.data["import_tariff"])
        export_tariff = float(call.data["export_tariff"])
        standing_charge = float(call.data.get("standing_charge", 0.0))
        if min(import_tariff, export_tariff, standing_charge) < 0:
            raise vol.Invalid("Tariff values cannot be negative")
        core.registry.data.setdefault("sources", {})["tariffs"] = {
            "enabled": True, "currency": str(call.data.get("currency", "CHF")).upper(),
            "import_tariff": import_tariff, "export_tariff": export_tariff,
            "standing_charge": standing_charge, "vat_included": bool(call.data.get("vat_included", True)),
            "saved_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        core.registry.data.setdefault("audit", []).append({"action": "save_tariff_settings"})
        await core.registry.async_save()
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)

    async def clear_tariff_settings(call: ServiceCall) -> None:
        core = _core(hass)
        core.registry.data.setdefault("sources", {})["tariffs"] = {"enabled": False, "currency": "CHF", "import_tariff": None, "export_tariff": None, "standing_charge": 0.0, "vat_included": True}
        core.registry.data.setdefault("audit", []).append({"action": "clear_tariff_settings"})
        await core.registry.async_save()
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)



    async def save_home_profile(call: ServiceCall) -> None:
        core = _core(hass)
        owner_name = str(call.data.get("owner_name", "")).strip()[:80]
        home_name = str(call.data.get("home_name", "Home")).strip()[:80] or "Home"
        settings = core.registry.data.setdefault("home_settings", {})
        settings.update({
            "owner_name": owner_name,
            "home_name": home_name,
            "use_owner_name": bool(call.data.get("use_owner_name", True)),
            "story_style": str(call.data.get("story_style", "friendly")),
            "briefing_length": str(call.data.get("briefing_length", "normal")),
        })
        core.registry.data.setdefault("audit", []).append({"action": "save_home_profile"})
        await core.registry.async_save()
        core.settings_api.refresh()
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)

    async def save_battery_capacity(call: ServiceCall) -> None:
        core = _core(hass)
        capacity = float(call.data["capacity_kwh"])
        if capacity <= 0 or capacity > 10000:
            raise vol.Invalid("Battery capacity must be greater than 0 and below 10000 kWh")
        core.registry.data.setdefault("home_settings", {})["battery_capacity_kwh"] = capacity
        core.registry.data.setdefault("audit", []).append({"action": "save_battery_capacity", "capacity_kwh": capacity})
        await core.registry.async_save()
        core.settings_api.refresh()
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)

    async def clear_battery_capacity(call: ServiceCall) -> None:
        core = _core(hass)
        core.registry.data.setdefault("home_settings", {})["battery_capacity_kwh"] = None
        core.registry.data.setdefault("audit", []).append({"action": "clear_battery_capacity"})
        await core.registry.async_save()
        core.settings_api.refresh()
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)


    async def set_data_epoch(call: ServiceCall) -> None:
        """Start Zeus trusted accounting at today or a selected local date."""
        from datetime import datetime
        from homeassistant.util import dt as dt_util
        from .period_authority import configure_data_epoch
        core = _core(hass)
        mode = str(call.data.get("mode", "today"))
        now = dt_util.now()
        if mode == "today":
            epoch = dt_util.start_of_local_day(now)
        else:
            raw = str(call.data.get("date", "")).strip()
            try:
                selected = datetime.strptime(raw, "%Y-%m-%d")
            except ValueError as err:
                raise vol.Invalid("date must use YYYY-MM-DD") from err
            epoch = selected.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            if epoch > now:
                raise vol.Invalid("Zeus data epoch cannot be in the future")
        settings = core.registry.data.setdefault("home_settings", {})
        settings["data_epoch"] = epoch.isoformat()
        core.registry.data.setdefault("audit", []).append({"action": "set_data_epoch", "epoch": epoch.isoformat()})
        await core.registry.async_save()
        configure_data_epoch(epoch)
        core.settings_api.refresh()
        await core.device_analytics.async_refresh_recorder_energy()
        core.device_analytics.refresh()
        await core.device_energy_attribution.async_refresh()
        await core.analytics.async_refresh_ha_energy_battery()
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)

    async def clear_data_epoch(call: ServiceCall) -> None:
        """Restore full available Recorder history as Zeus accounting scope."""
        from .period_authority import configure_data_epoch
        core = _core(hass)
        core.registry.data.setdefault("home_settings", {})["data_epoch"] = None
        core.registry.data.setdefault("audit", []).append({"action": "clear_data_epoch"})
        await core.registry.async_save()
        configure_data_epoch(None)
        core.settings_api.refresh()
        await core.device_analytics.async_refresh_recorder_energy()
        core.device_analytics.refresh()
        await core.device_energy_attribution.async_refresh()
        await core.analytics.async_refresh_ha_energy_battery()
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)

    async def save_entity_mapping(call: ServiceCall) -> None:
        core = _core(hass)
        result = core.energy_mapping.validate(call.data["field"], call.data["entity_id"])
        core.energy_mapping.last_test = result
        if result["status"] != "valid":
            core.device_import_manager.last_validation = {"status": "Save Rejected", "message": "Invalid mapping was not saved.", "issues": result["issues"], "mapping_test": result}
            core.energy_mapping.refresh()
            await _refresh_aion_entities(hass)
            return
        core.registry.data.setdefault("entity_mappings", {})[call.data["field"]] = call.data["entity_id"]
        if call.data["field"] in {"grid_power", "grid_import_power", "grid_export_power"}:
            mode = call.data.get("grid_mode", "bidirectional" if call.data["field"] == "grid_power" else "separate")
            core.registry.data.setdefault("mapping_options", {})["grid_mode"] = mode
        if call.data["field"] == "grid_power":
            convention = call.data.get("sign_convention", "positive_import")
            core.registry.data.setdefault("mapping_options", {})["grid_power_sign"] = convention
        if call.data["field"] in {"battery_power", "battery_charge_power", "battery_discharge_power"}:
            battery_mode = call.data.get("battery_mode", "bidirectional" if call.data["field"] == "battery_power" else "separate")
            core.registry.data.setdefault("mapping_options", {})["battery_mode"] = battery_mode
        if call.data["field"] == "battery_power":
            battery_convention = call.data.get("battery_sign_convention", "positive_discharge")
            core.registry.data.setdefault("mapping_options", {})["battery_power_sign"] = battery_convention
        core.registry.data.setdefault("audit", []).append({"action": "save_entity_mapping", "field": call.data["field"], "entity_id": call.data["entity_id"], "sign_convention": call.data.get("sign_convention")})
        await core.registry.async_save()
        core.update_engine.refresh_tracked_entities()
        core.refresh_pipeline()
        core.device_import_manager.last_validation = {"status": "Mapping Saved", "message": f"Saved {call.data['field']} -> {call.data['entity_id']}.", "issues": []}
        await _refresh_aion_entities(hass)

    async def clear_entity_mapping(call: ServiceCall) -> None:
        core = _core(hass)
        core.registry.data.setdefault("entity_mappings", {}).pop(call.data["field"], None)
        if call.data["field"] == "grid_power":
            core.registry.data.setdefault("mapping_options", {}).pop("grid_power_sign", None)
        if call.data["field"] == "battery_power":
            core.registry.data.setdefault("mapping_options", {}).pop("battery_power_sign", None)
        mappings = core.registry.data.setdefault("entity_mappings", {})
        if not mappings.get("grid_power") and not mappings.get("grid_import_power") and not mappings.get("grid_export_power"):
            core.registry.data.setdefault("mapping_options", {}).pop("grid_mode", None)
        if not mappings.get("battery_power") and not mappings.get("battery_charge_power") and not mappings.get("battery_discharge_power"):
            core.registry.data.setdefault("mapping_options", {}).pop("battery_mode", None)
        core.registry.data.setdefault("audit", []).append({"action": "clear_entity_mapping", "field": call.data["field"]})
        await core.registry.async_save()
        core.update_engine.refresh_tracked_entities()
        core.refresh_pipeline()
        core.device_import_manager.last_validation = {"status": "Mapping Cleared", "message": f"Cleared {call.data['field']}.", "issues": []}
        await _refresh_aion_entities(hass)

    async def register_battery_profile(call: ServiceCall) -> None:
        """Explicitly register canonical battery metadata for advisory modeling.

        This service writes metadata only. It never calls battery/inverter
        services and never changes SOC, reserve, charge or discharge controls.
        """
        core = _core(hass)
        mappings = core.registry.data.get("entity_mappings", {}) or {}
        soc_entity = call.data.get("soc_entity") or mappings.get("battery_soc")
        if not soc_entity:
            raise ValueError("A battery SOC entity must be mapped or supplied")
        profile = {
            "registered": True,
            "device_id": call.data.get("device_id", "canonical_battery"),
            "device_name": call.data.get("device_name", "Battery"),
            "device_type": "battery",
            "soc_entity": soc_entity,
            "capacity_kwh": float(call.data["capacity_kwh"]),
            "minimum_soc_percent": float(call.data["minimum_soc_percent"]),
            "emergency_reserve_percent": float(call.data["emergency_reserve_percent"]),
            "maximum_soc_percent": float(call.data["maximum_soc_percent"]),
            "max_charge_power_w": float(call.data["max_charge_power_w"]),
            "max_discharge_power_w": float(call.data["max_discharge_power_w"]),
            "round_trip_efficiency": float(call.data["round_trip_efficiency"]),
            "source": "explicit_user_registered_battery_profile",
            "recommendation_only": True,
        }
        if not 0 < profile["capacity_kwh"]:
            raise ValueError("capacity_kwh must be greater than 0")
        if not 0 <= profile["emergency_reserve_percent"] <= profile["minimum_soc_percent"] < profile["maximum_soc_percent"] <= 100:
            raise ValueError("SOC limits must satisfy emergency <= minimum < maximum within 0..100")
        if profile["max_charge_power_w"] <= 0 or profile["max_discharge_power_w"] <= 0:
            raise ValueError("charge/discharge limits must be greater than 0")
        if not 0.5 <= profile["round_trip_efficiency"] <= 1.0:
            raise ValueError("round_trip_efficiency must be between 0.5 and 1.0")
        core.registry.data.setdefault("home_settings", {})["battery_profile"] = profile
        core.registry.data.setdefault("audit", []).append({
            "action": "register_battery_profile",
            "device_id": profile["device_id"],
            "source": profile["source"],
        })
        await core.registry.async_save()
        core.refresh_pipeline()
        core.device_import_manager.last_validation = {
            "status": "Battery Profile Registered",
            "message": "Canonical battery metadata registered for recommendation-only Predictive Battery modeling.",
            "battery_profile": profile,
            "issues": [],
        }
        await _refresh_aion_entities(hass)

    async def clear_battery_profile(call: ServiceCall) -> None:
        core = _core(hass)
        core.registry.data.setdefault("home_settings", {}).pop("battery_profile", None)
        core.registry.data.setdefault("audit", []).append({"action": "clear_battery_profile"})
        await core.registry.async_save()
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)

    async def lifecycle_status(call: ServiceCall) -> None:
        """Publish the artifacts owned by the current AION EMS entry."""
        core = _core(hass)
        core.event_bus.publish(
            "LifecycleStatusRequested",
            "LifecycleManager",
            {
                "panel": "aion-ems",
                "registry_storage": "aion_ems_zeus.registry",
                "data_lake_storage": "aion_ems_zeus.data_lake",
                "frontend": "/config/www/aion_ems_zeus/device_manager.js",
                "clean_uninstall_supported": True,
            },
        )

    # Register services explicitly, once.
    hass.services.async_register(DOMAIN, SERVICE_EXPORT_REGISTRY, export_registry)
    hass.services.async_register(DOMAIN, SERVICE_BACKUP_REGISTRY, backup_registry)
    hass.services.async_register(DOMAIN, SERVICE_RESTORE_LATEST_BACKUP, restore_latest_backup)
    hass.services.async_register(DOMAIN, SERVICE_RELOAD_REGISTRY, reload_registry)

    hass.services.async_register(DOMAIN, SERVICE_REFRESH_ENTITY_DISCOVERY, refresh_entity_discovery)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_ENERGY_MAPPING, refresh_energy_mapping)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_INTEGRATION_HUB, refresh_integration_hub)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_DATA_BUS, refresh_data_bus)
    hass.services.async_register(DOMAIN, SERVICE_CAPTURE_DATA_LAKE_SNAPSHOT, capture_data_lake_snapshot)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_DATA_LAKE_SUMMARY, refresh_data_lake_summary)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_KNOWLEDGE_ENGINE, refresh_knowledge_engine)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_BRIEFING_CENTER, refresh_briefing_center)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_QUESTION_LIBRARY, refresh_question_library)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_CAPABILITY_REPORT, refresh_capability_report)
    hass.services.async_register(DOMAIN, SERVICE_PREVIEW_HA_ENERGY_IMPORT, preview_home_assistant_energy_import)
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_HA_ENERGY_IMPORT,
        apply_home_assistant_energy_import,
        schema=vol.Schema({
            vol.Optional("import_whole_home", default=True): cv.boolean,
            vol.Optional("import_devices", default=True): cv.boolean,
            vol.Optional("overwrite_existing", default=False): cv.boolean,
        }),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_REGISTER_BATTERY_PROFILE,
        register_battery_profile,
        schema=vol.Schema({
            vol.Optional("device_id", default="canonical_battery"): cv.string,
            vol.Optional("device_name", default="Battery"): cv.string,
            vol.Optional("soc_entity"): cv.entity_id,
            vol.Required("capacity_kwh"): vol.Coerce(float),
            vol.Required("minimum_soc_percent"): vol.Coerce(float),
            vol.Required("emergency_reserve_percent"): vol.Coerce(float),
            vol.Required("maximum_soc_percent"): vol.Coerce(float),
            vol.Required("max_charge_power_w"): vol.Coerce(float),
            vol.Required("max_discharge_power_w"): vol.Coerce(float),
            vol.Required("round_trip_efficiency"): vol.Coerce(float),
        }),
    )
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_BATTERY_PROFILE, clear_battery_profile)
    hass.services.async_register(DOMAIN, SERVICE_LIFECYCLE_STATUS, lifecycle_status)
    mapping_schema = vol.Schema({vol.Required("field"): vol.In(list(_core(hass).energy_mapping.FIELD_RULES)), vol.Required("entity_id"): cv.entity_id, vol.Optional("sign_convention"): vol.In(["positive_import", "positive_export"]), vol.Optional("grid_mode"): vol.In(["bidirectional", "separate"]), vol.Optional("battery_mode"): vol.In(["bidirectional", "separate"]), vol.Optional("battery_sign_convention"): vol.In(["positive_discharge", "positive_charge"])})
    hass.services.async_register(DOMAIN, SERVICE_TEST_ENTITY_MAPPING, test_entity_mapping, schema=mapping_schema)
    hass.services.async_register(DOMAIN, SERVICE_SAVE_ENTITY_MAPPING, save_entity_mapping, schema=mapping_schema)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_ENTITY_MAPPING, clear_entity_mapping, schema=vol.Schema({vol.Required("field"): vol.In(list(_core(hass).energy_mapping.FIELD_RULES))}))
    hass.services.async_register(DOMAIN, SERVICE_SAVE_WEATHER_SOURCE, save_weather_source, schema=vol.Schema({vol.Required("entity_id"): cv.entity_id}))
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_WEATHER_SOURCE, clear_weather_source)
    hass.services.async_register(DOMAIN, SERVICE_SAVE_HOME_PROFILE, save_home_profile, schema=vol.Schema({
        vol.Optional("owner_name", default=""): cv.string,
        vol.Optional("home_name", default="Home"): cv.string,
        vol.Optional("use_owner_name", default=True): cv.boolean,
        vol.Optional("story_style", default="friendly"): vol.In(["professional", "friendly", "technical", "concise"]),
        vol.Optional("briefing_length", default="normal"): vol.In(["short", "normal", "detailed"]),
    }))
    hass.services.async_register(
        DOMAIN,
        SERVICE_SAVE_TARIFF_SETTINGS,
        save_tariff_settings,
        schema=vol.Schema({
            vol.Required("currency"): cv.string,
            vol.Required("import_tariff"): vol.Coerce(float),
            vol.Required("export_tariff"): vol.Coerce(float),
            vol.Optional("standing_charge", default=0.0): vol.Coerce(float),
            vol.Optional("vat_included", default=True): cv.boolean,
        }),
    )
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_TARIFF_SETTINGS,
    SERVICE_SAVE_BATTERY_CAPACITY,
    SERVICE_SAVE_HOME_PROFILE,
    SERVICE_CLEAR_BATTERY_CAPACITY, clear_tariff_settings)

    hass.services.async_register(DOMAIN, SERVICE_IMPORT_DISCOVERY_CANDIDATE, import_discovery_candidate, schema=_device_schema())

    hass.services.async_register(DOMAIN, SERVICE_IMPORT_RECOMMENDED_DEVICES, import_recommended_devices, schema=vol.Schema({
        vol.Optional("dry_run", default=True): cv.boolean,
        vol.Optional("include_system_devices", default=True): cv.boolean,
        vol.Optional("include_appliances", default=True): cv.boolean,
        vol.Optional("overwrite_existing", default=False): cv.boolean,
    }))

    hass.services.async_register(DOMAIN, SERVICE_REFRESH_DEVICE_IMPORT_REVIEW, refresh_device_import_review, schema=vol.Schema({
        vol.Optional("include_system_devices", default=True): cv.boolean,
        vol.Optional("include_appliances", default=True): cv.boolean,
        vol.Optional("overwrite_existing", default=False): cv.boolean,
    }))

    hass.services.async_register(DOMAIN, SERVICE_IMPORT_REVIEWED_DEVICES, import_reviewed_devices, schema=vol.Schema({
        vol.Optional("dry_run", default=True): cv.boolean,
        vol.Optional("include_system_devices", default=True): cv.boolean,
        vol.Optional("include_appliances", default=True): cv.boolean,
        vol.Optional("overwrite_existing", default=False): cv.boolean,
    }))

    hass.services.async_register(DOMAIN, SERVICE_DEVICE_MANAGER_BUILD_REVIEW, device_manager_build_review, schema=vol.Schema({
        vol.Optional("include_system_devices", default=True): cv.boolean,
        vol.Optional("include_appliances", default=False): cv.boolean,
        vol.Optional("overwrite_existing", default=False): cv.boolean,
    }))

    hass.services.async_register(DOMAIN, SERVICE_DEVICE_MANAGER_IMPORT_READY, device_manager_import_ready, schema=vol.Schema({
        vol.Optional("dry_run", default=True): cv.boolean,
        vol.Optional("include_system_devices", default=True): cv.boolean,
        vol.Optional("include_appliances", default=False): cv.boolean,
        vol.Optional("overwrite_existing", default=False): cv.boolean,
    }))

    hass.services.async_register(DOMAIN, SERVICE_DEVICE_MANAGER_REMOVE_DEVICE, device_manager_remove_device, schema=vol.Schema({
        vol.Required("device_id"): cv.string,
    }))

    hass.services.async_register(DOMAIN, SERVICE_REMOVE_AUTO_IMPORTED_DEVICES, remove_auto_imported_devices, schema=vol.Schema({
        vol.Optional("dry_run", default=True): cv.boolean,
    }))

    hass.services.async_register(DOMAIN, SERVICE_ADD_DEVICE, add_device, schema=_device_schema())
    hass.services.async_register(DOMAIN, SERVICE_UPDATE_DEVICE, update_device, schema=_device_update_schema())
    hass.services.async_register(DOMAIN, SERVICE_REMOVE_DEVICE, remove_device, schema=vol.Schema({
        vol.Required("device_id"): cv.string,
    }))
    hass.services.async_register(DOMAIN, SERVICE_ADD_DEVICE_PREVIEW, add_device, schema=_device_schema())

    hass.services.async_register(DOMAIN, SERVICE_ADD_ROOM, add_room, schema=vol.Schema({
        vol.Required("room_id"): cv.string,
        vol.Required("name"): cv.string,
        vol.Optional("icon", default="mdi:home"): cv.icon,
        vol.Optional("notes", default=""): cv.string,
        vol.Optional("hybrid_inverter", default=False): cv.boolean,
        vol.Optional("solar_power_entity"): cv.entity_id,
    }))
    hass.services.async_register(DOMAIN, SERVICE_UPDATE_ROOM, update_room, schema=vol.Schema({
        vol.Required("room_id"): cv.string,
        vol.Required("name"): cv.string,
        vol.Optional("icon", default="mdi:home"): cv.icon,
        vol.Optional("notes", default=""): cv.string,
        vol.Optional("hybrid_inverter", default=False): cv.boolean,
        vol.Optional("solar_power_entity"): cv.entity_id,
    }))
    hass.services.async_register(DOMAIN, SERVICE_REMOVE_ROOM, remove_room, schema=vol.Schema({
        vol.Required("room_id"): cv.string,
    }))

    hass.services.async_register(DOMAIN, SERVICE_ADD_GROUP, add_group, schema=vol.Schema({
        vol.Required("group_id"): cv.string,
        vol.Required("name"): cv.string,
        vol.Optional("category", default="other"): cv.string,
        vol.Optional("priority", default="medium"): cv.string,
        vol.Optional("icon", default="mdi:group"): cv.icon,
        vol.Optional("notes", default=""): cv.string,
        vol.Optional("hybrid_inverter", default=False): cv.boolean,
        vol.Optional("solar_power_entity"): cv.entity_id,
    }))
    hass.services.async_register(DOMAIN, SERVICE_UPDATE_GROUP, update_group, schema=vol.Schema({
        vol.Required("group_id"): cv.string,
        vol.Required("name"): cv.string,
        vol.Optional("category", default="other"): cv.string,
        vol.Optional("priority", default="medium"): cv.string,
        vol.Optional("icon", default="mdi:group"): cv.icon,
        vol.Optional("notes", default=""): cv.string,
        vol.Optional("hybrid_inverter", default=False): cv.boolean,
        vol.Optional("solar_power_entity"): cv.entity_id,
    }))
    hass.services.async_register(DOMAIN, SERVICE_REMOVE_GROUP, remove_group, schema=vol.Schema({
        vol.Required("group_id"): cv.string,
    }))

    hass.services.async_register(DOMAIN, SERVICE_HELIOS_MIGRATION_ANALYZE, helios_migration_analyze)
    hass.services.async_register(DOMAIN, SERVICE_HELIOS_MIGRATION_PREVIEW, helios_migration_preview)
    hass.services.async_register(DOMAIN, SERVICE_HELIOS_SMART_IMPORT_REPORT, helios_smart_import_report)
    hass.services.async_register(DOMAIN, SERVICE_APPLY_HELIOS_MIGRATION_PREVIEW, apply_helios_migration_preview)

    hass.services.async_register(DOMAIN, SERVICE_REFRESH_FORECAST, refresh_preview)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_OPTIMIZER_PREVIEW, refresh_preview)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_SCHEDULER_PREVIEW, refresh_preview)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_LEARNING_PREVIEW, refresh_preview)

    # Verify the finance services explicitly; these are required by Sources → Tariffs.
    if not hass.services.has_service(DOMAIN, SERVICE_SAVE_TARIFF_SETTINGS):
        hass.services.async_register(DOMAIN, SERVICE_SAVE_TARIFF_SETTINGS, save_tariff_settings, schema=vol.Schema({
            vol.Required("currency"): cv.string,
            vol.Required("import_tariff"): vol.Coerce(float),
            vol.Required("export_tariff"): vol.Coerce(float),
            vol.Optional("standing_charge", default=0.0): vol.Coerce(float),
            vol.Optional("vat_included", default=True): cv.boolean,
        }))
    if not hass.services.has_service(DOMAIN, SERVICE_CLEAR_TARIFF_SETTINGS):
        hass.services.async_register(DOMAIN, SERVICE_CLEAR_TARIFF_SETTINGS,
    SERVICE_SAVE_BATTERY_CAPACITY,
    SERVICE_SAVE_HOME_PROFILE,
    SERVICE_CLEAR_BATTERY_CAPACITY, clear_tariff_settings)


    if not hass.services.has_service(DOMAIN, SERVICE_SAVE_BATTERY_CAPACITY):
        hass.services.async_register(DOMAIN, SERVICE_SAVE_BATTERY_CAPACITY, save_battery_capacity, schema=vol.Schema({
            vol.Required("capacity_kwh"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=10000)),
        }))
    if not hass.services.has_service(DOMAIN, SERVICE_CLEAR_BATTERY_CAPACITY):
        hass.services.async_register(DOMAIN, SERVICE_CLEAR_BATTERY_CAPACITY, clear_battery_capacity)


    if not hass.services.has_service(DOMAIN, SERVICE_SET_DATA_EPOCH):
        hass.services.async_register(DOMAIN, SERVICE_SET_DATA_EPOCH, set_data_epoch, schema=vol.Schema({
            vol.Optional("mode", default="today"): vol.In(["today", "date"]),
            vol.Optional("date"): cv.string,
        }))
    if not hass.services.has_service(DOMAIN, SERVICE_CLEAR_DATA_EPOCH):
        hass.services.async_register(DOMAIN, SERVICE_CLEAR_DATA_EPOCH, clear_data_epoch)

    hass.services.async_register(DOMAIN, SERVICE_SAVE_NOTIFICATION_SETTINGS, save_notification_settings, schema=vol.Schema({
        vol.Optional("enabled"): cv.boolean, vol.Optional("persistent_enabled"): cv.boolean, vol.Optional("mobile_enabled"): cv.boolean,
        vol.Optional("mobile_targets"): vol.All(cv.ensure_list,[cv.string]), vol.Optional("quiet_hours_enabled"): cv.boolean,
        vol.Optional("quiet_start"): cv.string, vol.Optional("quiet_end"): cv.string, vol.Optional("confidence_threshold"): vol.All(vol.Coerce(int),vol.Range(min=0,max=100)),
        vol.Optional("cooldown_minutes"): vol.All(vol.Coerce(int),vol.Range(min=1,max=1440)),
        **{vol.Optional("category_"+k):cv.boolean for k in ("recommendation","battery","scheduler","high_grid_import","solar_surplus","tariff","daily_report","system_health")}
    }))
    hass.services.async_register(DOMAIN, SERVICE_TEST_NOTIFICATION, test_notification)
    hass.services.async_register(DOMAIN, SERVICE_SAVE_PLUGIN_SETTINGS, save_plugin_settings, schema=vol.Schema({
        vol.Required("plugin_id"): vol.In(["email","pushover","shelly","zigbee","mqtt","nas_backup","inverter_adapters"]),
        vol.Optional("enabled"): cv.boolean, vol.Optional("service"): cv.string, vol.Optional("path"): cv.string,
        vol.Optional("server"): cv.string, vol.Optional("protocol"): vol.In(["smb","nfs"]), vol.Optional("nas_type"): vol.In(["synology","qnap","truenas","unraid","generic"]),
        vol.Optional("remote_path"): cv.string, vol.Optional("share"): cv.string, vol.Optional("folder"): cv.string, vol.Optional("username"): cv.string,
        vol.Optional("retention"): vol.All(vol.Coerce(int),vol.Range(min=1,max=100)), vol.Optional("schedule"): cv.string,
    }))
    hass.services.async_register(DOMAIN, SERVICE_TEST_PLUGIN, test_plugin, schema=vol.Schema({vol.Required("plugin_id"): cv.string}))
    hass.services.async_register(DOMAIN, SERVICE_CREATE_NAS_BACKUP, create_nas_backup)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_PLUGIN_DISCOVERY, refresh_plugin_discovery, schema=vol.Schema({vol.Optional("plugin_id"): vol.In(["email","pushover","shelly","zigbee","mqtt","nas_backup","inverter_adapters"])}))
    hass.services.async_register(DOMAIN, SERVICE_RUN_QA_HEALTH_CHECK, run_qa_health_check)
    REGISTERED = True

async def async_unload_services(hass: HomeAssistant) -> None:
    """Remove AION EMS services."""
    global REGISTERED
    if not REGISTERED:
        return
    services = [
        SERVICE_EXPORT_REGISTRY, SERVICE_BACKUP_REGISTRY, SERVICE_RESTORE_LATEST_BACKUP, SERVICE_RELOAD_REGISTRY,
        SERVICE_REFRESH_ENTITY_DISCOVERY,
    SERVICE_REFRESH_ENERGY_MAPPING,
    SERVICE_REFRESH_ENERGY_FLOW, SERVICE_REFRESH_INTEGRATION_HUB, SERVICE_REFRESH_DATA_BUS,
        SERVICE_CAPTURE_DATA_LAKE_SNAPSHOT, SERVICE_REFRESH_DATA_LAKE_SUMMARY, SERVICE_REFRESH_KNOWLEDGE_ENGINE,
        SERVICE_REFRESH_BRIEFING_CENTER, SERVICE_REFRESH_QUESTION_LIBRARY, SERVICE_REFRESH_CAPABILITY_REPORT,
        SERVICE_IMPORT_DISCOVERY_CANDIDATE, SERVICE_IMPORT_RECOMMENDED_DEVICES,
    SERVICE_REFRESH_DEVICE_IMPORT_REVIEW,
    SERVICE_IMPORT_REVIEWED_DEVICES,
    SERVICE_DEVICE_MANAGER_BUILD_REVIEW,
    SERVICE_DEVICE_MANAGER_IMPORT_READY,
    SERVICE_DEVICE_MANAGER_REMOVE_DEVICE, SERVICE_REMOVE_AUTO_IMPORTED_DEVICES,
        SERVICE_HELIOS_MIGRATION_ANALYZE, SERVICE_HELIOS_MIGRATION_PREVIEW, SERVICE_HELIOS_SMART_IMPORT_REPORT,
        SERVICE_APPLY_HELIOS_MIGRATION_PREVIEW, SERVICE_ADD_DEVICE, SERVICE_UPDATE_DEVICE, SERVICE_REMOVE_DEVICE,
        SERVICE_ADD_ROOM, SERVICE_UPDATE_ROOM, SERVICE_REMOVE_ROOM, SERVICE_ADD_GROUP, SERVICE_UPDATE_GROUP, SERVICE_REMOVE_GROUP,
        SERVICE_ADD_DEVICE_PREVIEW, SERVICE_REFRESH_FORECAST, SERVICE_REFRESH_OPTIMIZER_PREVIEW, SERVICE_REFRESH_SCHEDULER_PREVIEW,
        SERVICE_REFRESH_LEARNING_PREVIEW,
        SERVICE_SAVE_WEATHER_SOURCE, SERVICE_CLEAR_WEATHER_SOURCE,
        SERVICE_SAVE_TARIFF_SETTINGS, SERVICE_CLEAR_TARIFF_SETTINGS, SERVICE_SAVE_HOME_PROFILE,
    SERVICE_SAVE_BATTERY_CAPACITY,
    SERVICE_SAVE_HOME_PROFILE,
    SERVICE_CLEAR_BATTERY_CAPACITY, SERVICE_SET_DATA_EPOCH, SERVICE_CLEAR_DATA_EPOCH, SERVICE_SAVE_NOTIFICATION_SETTINGS, SERVICE_TEST_NOTIFICATION,
        SERVICE_SAVE_PLUGIN_SETTINGS, SERVICE_TEST_PLUGIN, SERVICE_CREATE_NAS_BACKUP, SERVICE_REFRESH_PLUGIN_DISCOVERY, SERVICE_RUN_QA_HEALTH_CHECK,
        SERVICE_LIFECYCLE_STATUS,
        SERVICE_PREVIEW_HA_ENERGY_IMPORT, SERVICE_APPLY_HA_ENERGY_IMPORT,
    ]
    for service in services:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    REGISTERED = False
