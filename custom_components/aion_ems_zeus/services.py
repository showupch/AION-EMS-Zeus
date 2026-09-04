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
    SERVICE_SET_ENERGY_PRICES,
    SERVICE_CLEAR_DYNAMIC_TARIFF,
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
    SERVICE_SAVE_SWITCH_HUB_DEVICE,
    SERVICE_REMOVE_SWITCH_HUB_DEVICE,
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
                "sensor.aion_ems_zeus_switch_hub",
            ]
        },
        blocking=True,
    )


def _device_schema(require_all=True):
    req = vol.Required if require_all else vol.Optional
    return vol.Schema({
        req("device_id"): cv.string,
        req("name"): cv.string,
        req("power_entity"): vol.Any("", cv.entity_id),
        req("energy_entity"): vol.Any("", cv.entity_id),
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
        vol.Optional("heating_electrical_power_entity"): cv.entity_id,
        vol.Optional("heating_thermal_power_entity"): cv.entity_id,
        vol.Optional("heating_electrical_energy_entity"): cv.entity_id,
        vol.Optional("heating_thermal_energy_entity"): cv.entity_id,
        vol.Optional("dhw_electrical_power_entity"): cv.entity_id,
        vol.Optional("dhw_thermal_power_entity"): cv.entity_id,
        vol.Optional("dhw_electrical_energy_entity"): cv.entity_id,
        vol.Optional("dhw_thermal_energy_entity"): cv.entity_id,
        vol.Optional("cooling_electrical_power_entity"): cv.entity_id,
        vol.Optional("cooling_electrical_energy_entity"): cv.entity_id,
        vol.Optional("cooling_thermal_power_entity"): cv.entity_id,
        vol.Optional("cooling_thermal_energy_entity"): cv.entity_id,
        vol.Optional("separate_heating_dhw_measurements", default=False): cv.boolean,
        vol.Optional("cooling_measurements_enabled", default=False): cv.boolean,
        vol.Optional("operating_mode_entity"): cv.entity_id,
        vol.Optional("target_temperature_entity"): cv.entity_id,
        vol.Optional("jaz_entity"): cv.entity_id,
        vol.Optional("heat_carrier_forward_entity"): cv.entity_id,
        vol.Optional("heat_carrier_return_entity"): cv.entity_id,
        vol.Optional("source_in_temperature_entity"): cv.entity_id,
        vol.Optional("source_out_temperature_entity"): cv.entity_id,
        vol.Optional("source_pump_speed_entity"): cv.entity_id,
        vol.Optional("heat_carrier_pump_speed_entity"): cv.entity_id,
        vol.Optional("compressor_activity_entity"): cv.entity_id,
        vol.Optional("compressor_speed_entity"): cv.entity_id,
        vol.Optional("compressor_target_speed_entity"): cv.entity_id,
        vol.Optional("dhw_target_temperature_entity"): cv.entity_id,
        vol.Optional("controllable", default=False): cv.boolean,
        vol.Optional("control_permission", default=False): cv.boolean,
        vol.Optional("actuator_type"): vol.In(["entity", "service", "modbus", "mqtt"]),
        vol.Optional("control_entity"): cv.entity_id,
        vol.Optional("control_service"): cv.string,
        vol.Optional("control_min_power_w"): vol.Coerce(float),
        vol.Optional("control_max_power_w"): vol.Coerce(float),
        vol.Optional("control_hub"): cv.string,
        vol.Optional("control_elwa_ip"): cv.string,
        vol.Optional("control_unit"): vol.Coerce(int),
        vol.Optional("control_address"): vol.Coerce(int),
        vol.Optional("control_boiler_temperature_entity"): cv.entity_id,
        vol.Optional("control_element_temperature_entity"): cv.entity_id,
        vol.Optional("control_surplus_entity"): cv.entity_id,
        vol.Optional("control_lockout_entity"): cv.entity_id,
        vol.Optional("control_stop_temperature_c"): vol.Coerce(float),
        vol.Optional("control_restart_temperature_c"): vol.Coerce(float),
        vol.Optional("device_profile"): cv.string,
        vol.Optional("control_solar_start_threshold_w"): vol.Coerce(float),
        vol.Optional("control_solar_factor"): vol.Coerce(float),
        vol.Optional("control_solar_export_reserve_w"): vol.Coerce(float),
        vol.Optional("control_element_taper_start_c"): vol.Coerce(float),
        vol.Optional("control_element_hard_stop_c"): vol.Coerce(float),
        vol.Optional("control_grid_backup_start_c"): vol.Coerce(float),
        vol.Optional("control_grid_backup_stop_c"): vol.Coerce(float),
        vol.Optional("control_keepalive_interval_s"): vol.Coerce(float),
        vol.Optional("control_owner"): vol.In(["home_assistant", "zeus"]),
        vol.Optional("control_previous_controller_entity"): cv.entity_id,
        vol.Optional("control_handover_confirmed", default=False): cv.boolean,
        vol.Optional("control_execution_arm_requested", default=False): cv.boolean,
        vol.Optional("control_execution_arm_confirmed", default=False): cv.boolean,
        vol.Optional("control_execution_master_enabled", default=False): cv.boolean,
        vol.Optional("control_emergency_stop", default=False): cv.boolean,
        vol.Optional("control_goe_id"): cv.string,
        vol.Optional("control_mqtt_topic"): cv.string,
        vol.Optional("control_grid_power_entity"): cv.entity_id,
        vol.Optional("control_battery_power_entity"): cv.entity_id,
        vol.Optional("control_publish_interval_s"): vol.Coerce(float),
    })


def _entity_id_or_empty(value):
    """Accept a valid HA entity id or an explicit empty string for editor clears."""
    if value == "":
        return ""
    return cv.entity_id(value)


def _device_update_schema():
    """Device update schema without injected defaults.

    Defaults are correct for Add Device, but unsafe for Update Device because
    they can overwrite persisted metadata/mappings when an older frontend omits
    a field. The update handler merges only fields actually supplied.
    """
    return vol.Schema({
        vol.Required("device_id"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("power_entity"): _entity_id_or_empty,
        vol.Optional("energy_entity"): _entity_id_or_empty,
        vol.Optional("energy_type"): vol.In(["auto", "daily", "total_increasing"]),
        vol.Optional("state_entity"): _entity_id_or_empty,
        vol.Optional("availability_entity"): _entity_id_or_empty,
        vol.Optional("device_type"): cv.string,
        vol.Optional("category"): cv.string,
        vol.Optional("room_id"): cv.string,
        vol.Optional("group_ids"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("priority"): cv.string,
        vol.Optional("icon"): cv.icon,
        vol.Optional("enabled"): cv.boolean,
        vol.Optional("notes"): cv.string,
        vol.Optional("hybrid_inverter"): cv.boolean,
        vol.Optional("solar_power_entity"): _entity_id_or_empty,
        vol.Optional("temperature_entity"): _entity_id_or_empty,
        vol.Optional("cop_entity"): _entity_id_or_empty,
        vol.Optional("thermal_power_entity"): _entity_id_or_empty,
        vol.Optional("thermal_energy_entity"): _entity_id_or_empty,
        vol.Optional("supply_temperature_entity"): _entity_id_or_empty,
        vol.Optional("return_temperature_entity"): _entity_id_or_empty,
        vol.Optional("outdoor_temperature_entity"): _entity_id_or_empty,
        vol.Optional("compressor_state_entity"): _entity_id_or_empty,
        vol.Optional("compressor_runtime_entity"): _entity_id_or_empty,
        vol.Optional("compressor_starts_entity"): _entity_id_or_empty,
        vol.Optional("dhw_temperature_entity"): _entity_id_or_empty,
        vol.Optional("dhw_energy_entity"): _entity_id_or_empty,
        vol.Optional("heating_energy_entity"): _entity_id_or_empty,
        vol.Optional("cooling_energy_entity"): _entity_id_or_empty,
        vol.Optional("heating_electrical_power_entity"): _entity_id_or_empty,
        vol.Optional("heating_thermal_power_entity"): _entity_id_or_empty,
        vol.Optional("heating_electrical_energy_entity"): _entity_id_or_empty,
        vol.Optional("heating_thermal_energy_entity"): _entity_id_or_empty,
        vol.Optional("dhw_electrical_power_entity"): _entity_id_or_empty,
        vol.Optional("dhw_thermal_power_entity"): _entity_id_or_empty,
        vol.Optional("dhw_electrical_energy_entity"): _entity_id_or_empty,
        vol.Optional("dhw_thermal_energy_entity"): _entity_id_or_empty,
        vol.Optional("cooling_electrical_power_entity"): _entity_id_or_empty,
        vol.Optional("cooling_electrical_energy_entity"): _entity_id_or_empty,
        vol.Optional("cooling_thermal_power_entity"): _entity_id_or_empty,
        vol.Optional("cooling_thermal_energy_entity"): _entity_id_or_empty,
        vol.Optional("separate_heating_dhw_measurements"): cv.boolean,
        vol.Optional("cooling_measurements_enabled"): cv.boolean,
        vol.Optional("operating_mode_entity"): _entity_id_or_empty,
        vol.Optional("target_temperature_entity"): _entity_id_or_empty,
        vol.Optional("jaz_entity"): _entity_id_or_empty,
        vol.Optional("heat_carrier_forward_entity"): _entity_id_or_empty,
        vol.Optional("heat_carrier_return_entity"): _entity_id_or_empty,
        vol.Optional("source_in_temperature_entity"): _entity_id_or_empty,
        vol.Optional("source_out_temperature_entity"): _entity_id_or_empty,
        vol.Optional("source_pump_speed_entity"): _entity_id_or_empty,
        vol.Optional("heat_carrier_pump_speed_entity"): _entity_id_or_empty,
        vol.Optional("compressor_activity_entity"): _entity_id_or_empty,
        vol.Optional("compressor_speed_entity"): _entity_id_or_empty,
        vol.Optional("compressor_target_speed_entity"): _entity_id_or_empty,
        vol.Optional("dhw_target_temperature_entity"): _entity_id_or_empty,
        vol.Optional("controllable"): cv.boolean,
        vol.Optional("control_permission"): cv.boolean,
        vol.Optional("actuator_type"): vol.In(["entity", "service", "modbus", "mqtt"]),
        vol.Optional("control_entity"): cv.entity_id,
        vol.Optional("control_service"): cv.string,
        vol.Optional("control_min_power_w"): vol.Coerce(float),
        vol.Optional("control_max_power_w"): vol.Coerce(float),
        vol.Optional("control_hub"): cv.string,
        vol.Optional("control_elwa_ip"): cv.string,
        vol.Optional("control_unit"): vol.Coerce(int),
        vol.Optional("control_address"): vol.Coerce(int),
        vol.Optional("control_boiler_temperature_entity"): _entity_id_or_empty,
        vol.Optional("control_element_temperature_entity"): _entity_id_or_empty,
        vol.Optional("control_surplus_entity"): _entity_id_or_empty,
        vol.Optional("control_lockout_entity"): _entity_id_or_empty,
        vol.Optional("control_stop_temperature_c"): vol.Coerce(float),
        vol.Optional("control_restart_temperature_c"): vol.Coerce(float),
        vol.Optional("device_profile"): cv.string,
        vol.Optional("control_solar_start_threshold_w"): vol.Coerce(float),
        vol.Optional("control_solar_factor"): vol.Coerce(float),
        vol.Optional("control_solar_export_reserve_w"): vol.Coerce(float),
        vol.Optional("control_element_taper_start_c"): vol.Coerce(float),
        vol.Optional("control_element_hard_stop_c"): vol.Coerce(float),
        vol.Optional("control_grid_backup_start_c"): vol.Coerce(float),
        vol.Optional("control_grid_backup_stop_c"): vol.Coerce(float),
        vol.Optional("control_keepalive_interval_s"): vol.Coerce(float),
        vol.Optional("control_owner"): vol.In(["home_assistant", "zeus"]),
        vol.Optional("control_previous_controller_entity"): _entity_id_or_empty,
        vol.Optional("control_handover_confirmed"): cv.boolean,
        vol.Optional("control_execution_arm_requested"): cv.boolean,
        vol.Optional("control_execution_arm_confirmed"): cv.boolean,
        vol.Optional("control_execution_master_enabled"): cv.boolean,
        vol.Optional("control_emergency_stop"): cv.boolean,
        vol.Optional("control_goe_id"): cv.string,
        vol.Optional("control_mqtt_topic"): cv.string,
        vol.Optional("control_grid_power_entity"): _entity_id_or_empty,
        vol.Optional("control_battery_power_entity"): _entity_id_or_empty,
        vol.Optional("control_publish_interval_s"): vol.Coerce(float),
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
        await core.qa_diagnostics.run()
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
            heating_electrical_power_entity=call.data.get("heating_electrical_power_entity"),
            heating_thermal_power_entity=call.data.get("heating_thermal_power_entity"),
            heating_electrical_energy_entity=call.data.get("heating_electrical_energy_entity"),
            heating_thermal_energy_entity=call.data.get("heating_thermal_energy_entity"),
            dhw_electrical_power_entity=call.data.get("dhw_electrical_power_entity"),
            dhw_thermal_power_entity=call.data.get("dhw_thermal_power_entity"),
            dhw_electrical_energy_entity=call.data.get("dhw_electrical_energy_entity"),
            dhw_thermal_energy_entity=call.data.get("dhw_thermal_energy_entity"),
            cooling_electrical_power_entity=call.data.get("cooling_electrical_power_entity"),
            cooling_electrical_energy_entity=call.data.get("cooling_electrical_energy_entity"),
            cooling_thermal_power_entity=call.data.get("cooling_thermal_power_entity"),
            cooling_thermal_energy_entity=call.data.get("cooling_thermal_energy_entity"),
            separate_heating_dhw_measurements=call.data.get("separate_heating_dhw_measurements", False),
            cooling_measurements_enabled=call.data.get("cooling_measurements_enabled", False),
            operating_mode_entity=call.data.get("operating_mode_entity"),
            target_temperature_entity=call.data.get("target_temperature_entity"),
            jaz_entity=call.data.get("jaz_entity"),
            heat_carrier_forward_entity=call.data.get("heat_carrier_forward_entity"),
            heat_carrier_return_entity=call.data.get("heat_carrier_return_entity"),
            source_in_temperature_entity=call.data.get("source_in_temperature_entity"),
            source_out_temperature_entity=call.data.get("source_out_temperature_entity"),
            source_pump_speed_entity=call.data.get("source_pump_speed_entity"),
            heat_carrier_pump_speed_entity=call.data.get("heat_carrier_pump_speed_entity"),
            compressor_activity_entity=call.data.get("compressor_activity_entity"),
            compressor_speed_entity=call.data.get("compressor_speed_entity"),
            compressor_target_speed_entity=call.data.get("compressor_target_speed_entity"),
            dhw_target_temperature_entity=call.data.get("dhw_target_temperature_entity"),
            controllable=call.data.get("controllable", False),
            control_permission=call.data.get("control_permission", False),
            control_dual_permission_armed=(
                bool(call.data.get("controllable", False))
                and bool(call.data.get("control_permission", False))
                and str(call.data.get("device_type") or "") == "ev_charger"
                and str(call.data.get("device_profile") or "") == "go_e_charger_mqtt"
            ),
            actuator_type=call.data.get("actuator_type"),
            control_entity=call.data.get("control_entity"),
            control_service=call.data.get("control_service"),
            control_min_power_w=call.data.get("control_min_power_w"),
            control_max_power_w=call.data.get("control_max_power_w"),
            control_hub=call.data.get("control_hub"),
            control_elwa_ip=call.data.get("control_elwa_ip"),
            control_unit=call.data.get("control_unit"),
            control_address=call.data.get("control_address"),
            control_boiler_temperature_entity=call.data.get("control_boiler_temperature_entity"),
            control_element_temperature_entity=call.data.get("control_element_temperature_entity"),
            control_surplus_entity=call.data.get("control_surplus_entity"),
            control_lockout_entity=call.data.get("control_lockout_entity"),
            control_stop_temperature_c=call.data.get("control_stop_temperature_c"),
            control_restart_temperature_c=call.data.get("control_restart_temperature_c"),
            device_profile=call.data.get("device_profile"),
            control_solar_start_threshold_w=call.data.get("control_solar_start_threshold_w"),
            control_solar_factor=call.data.get("control_solar_factor"),
            control_solar_export_reserve_w=call.data.get("control_solar_export_reserve_w"),
            control_element_taper_start_c=call.data.get("control_element_taper_start_c"),
            control_element_hard_stop_c=call.data.get("control_element_hard_stop_c"),
            control_grid_backup_start_c=call.data.get("control_grid_backup_start_c"),
            control_grid_backup_stop_c=call.data.get("control_grid_backup_stop_c"),
            control_keepalive_interval_s=call.data.get("control_keepalive_interval_s"),
            control_owner=call.data.get("control_owner"),
            control_previous_controller_entity=call.data.get("control_previous_controller_entity"),
            control_handover_confirmed=call.data.get("control_handover_confirmed", False),
            control_execution_arm_requested=call.data.get("control_execution_arm_requested", False),
            control_execution_arm_confirmed=call.data.get("control_execution_arm_confirmed", False),
            control_execution_master_enabled=call.data.get("control_execution_master_enabled", False),
            control_emergency_stop=call.data.get("control_emergency_stop", False),
            control_goe_id=call.data.get("control_goe_id"),
            control_mqtt_topic=call.data.get("control_mqtt_topic"),
            control_grid_power_entity=call.data.get("control_grid_power_entity"),
            control_battery_power_entity=call.data.get("control_battery_power_entity"),
            control_publish_interval_s=call.data.get("control_publish_interval_s"),
        )
        issues = await core.registry.async_add_device(device)
        core.device_import_manager.last_validation = {"status": "Imported" if not any(i["severity"] == "error" for i in issues) else "Error", "device": device, "issues": issues, "message": "Device import completed."}
        core.update_engine.refresh_tracked_entities()
        core.refresh_pipeline()
        await core.smart_control.async_evaluate_execution()

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
        # alpha.6: an explicitly cleared optional entity mapping is a real update.
        # Cached/older frontends may omit fields, but the editor sends an empty
        # string when the operator deliberately removes a mapping. Persist that
        # as None instead of resurrecting the previous entity.
        clearable_entity_fields = (
            "power_entity", "energy_entity",
            "state_entity", "availability_entity", "solar_power_entity",
            "temperature_entity", "cop_entity", "thermal_power_entity", "thermal_energy_entity",
            "supply_temperature_entity", "return_temperature_entity", "outdoor_temperature_entity",
            "compressor_state_entity", "compressor_runtime_entity", "compressor_starts_entity",
            "dhw_temperature_entity", "dhw_energy_entity", "heating_energy_entity", "cooling_energy_entity",
            "heating_electrical_power_entity", "heating_thermal_power_entity",
            "heating_electrical_energy_entity", "heating_thermal_energy_entity",
            "dhw_electrical_power_entity", "dhw_thermal_power_entity",
            "dhw_electrical_energy_entity", "dhw_thermal_energy_entity",
            "cooling_electrical_power_entity", "cooling_electrical_energy_entity",
            "cooling_thermal_power_entity", "cooling_thermal_energy_entity",
            "operating_mode_entity", "target_temperature_entity", "jaz_entity",
            "heat_carrier_forward_entity", "heat_carrier_return_entity",
            "source_in_temperature_entity", "source_out_temperature_entity", "source_pump_speed_entity", "heat_carrier_pump_speed_entity",
            "compressor_activity_entity", "compressor_speed_entity", "compressor_target_speed_entity",
            "dhw_target_temperature_entity",
            "control_boiler_temperature_entity", "control_element_temperature_entity",
            "control_surplus_entity", "control_lockout_entity",
            "control_previous_controller_entity",
        )
        for key in clearable_entity_fields:
            if key in call.data and isinstance(call.data.get(key), str) and not call.data.get(key).strip():
                merged[key] = None
        # Control numeric metadata is safety-critical. Never replace a valid
        # persisted Modbus/power/temperature value with a missing/non-finite one.
        import math
        for key in (
            "control_min_power_w", "control_max_power_w", "control_unit",
            "control_address", "control_stop_temperature_c",
            "control_restart_temperature_c", "control_solar_start_threshold_w",
            "control_solar_factor", "control_solar_export_reserve_w", "control_element_taper_start_c",
            "control_element_hard_stop_c", "control_grid_backup_start_c",
            "control_grid_backup_stop_c", "control_keepalive_interval_s",
        ):
            value = merged.get(key)
            invalid = value is None
            if isinstance(value, float) and not math.isfinite(value):
                invalid = True
            if invalid and existing.get(key) is not None:
                merged[key] = existing.get(key)
        # alpha.7 hard gate: go-e MQTT execution receives a separate server-side
        # dual-permission latch.  It is armed only when BOTH checkboxes are true
        # in the same persisted update.  Missing/false permission input always
        # disarms the latch (fail closed), preventing a stale true value from
        # keeping the MQTT publisher alive.
        merged_type = str(merged.get("device_type", merged.get("type", existing.get("type", ""))) or "")
        merged_profile = str(merged.get("device_profile", existing.get("device_profile", "")) or "")
        if merged_type == "ev_charger" and merged_profile == "go_e_charger_mqtt":
            merged["controllable"] = bool(call.data.get("controllable", False))
            merged["control_permission"] = bool(call.data.get("control_permission", False))
            merged["control_dual_permission_armed"] = bool(
                merged["controllable"] and merged["control_permission"]
            )
        else:
            merged["control_dual_permission_armed"] = False

        # Build through the canonical registry constructor so types and energy
        # classification remain normalized, but preserve every known mapping.
        device = core.registry.build_device(
            device_id=device_id,
            name=merged.get("name") or existing.get("name") or device_id,
            power_entity=merged.get("power_entity"),
            energy_entity=merged.get("energy_entity"),
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
            heating_electrical_power_entity=merged.get("heating_electrical_power_entity", existing.get("heating_electrical_power_entity")),
            heating_thermal_power_entity=merged.get("heating_thermal_power_entity", existing.get("heating_thermal_power_entity")),
            heating_electrical_energy_entity=merged.get("heating_electrical_energy_entity", existing.get("heating_electrical_energy_entity")),
            heating_thermal_energy_entity=merged.get("heating_thermal_energy_entity", existing.get("heating_thermal_energy_entity")),
            dhw_electrical_power_entity=merged.get("dhw_electrical_power_entity", existing.get("dhw_electrical_power_entity")),
            dhw_thermal_power_entity=merged.get("dhw_thermal_power_entity", existing.get("dhw_thermal_power_entity")),
            dhw_electrical_energy_entity=merged.get("dhw_electrical_energy_entity", existing.get("dhw_electrical_energy_entity")),
            dhw_thermal_energy_entity=merged.get("dhw_thermal_energy_entity", existing.get("dhw_thermal_energy_entity")),
            cooling_electrical_power_entity=merged.get("cooling_electrical_power_entity", existing.get("cooling_electrical_power_entity")),
            cooling_electrical_energy_entity=merged.get("cooling_electrical_energy_entity", existing.get("cooling_electrical_energy_entity")),
            cooling_thermal_power_entity=merged.get("cooling_thermal_power_entity", existing.get("cooling_thermal_power_entity")),
            cooling_thermal_energy_entity=merged.get("cooling_thermal_energy_entity", existing.get("cooling_thermal_energy_entity")),
            separate_heating_dhw_measurements=merged.get("separate_heating_dhw_measurements", existing.get("separate_heating_dhw_measurements", False)),
            cooling_measurements_enabled=merged.get("cooling_measurements_enabled", existing.get("cooling_measurements_enabled", False)),
            operating_mode_entity=merged.get("operating_mode_entity", existing.get("operating_mode_entity")),
            target_temperature_entity=merged.get("target_temperature_entity", existing.get("target_temperature_entity")),
            jaz_entity=merged.get("jaz_entity", existing.get("jaz_entity")),
            heat_carrier_forward_entity=merged.get("heat_carrier_forward_entity", existing.get("heat_carrier_forward_entity")),
            heat_carrier_return_entity=merged.get("heat_carrier_return_entity", existing.get("heat_carrier_return_entity")),
            source_in_temperature_entity=merged.get("source_in_temperature_entity", existing.get("source_in_temperature_entity")),
            source_out_temperature_entity=merged.get("source_out_temperature_entity", existing.get("source_out_temperature_entity")),
            source_pump_speed_entity=merged.get("source_pump_speed_entity", existing.get("source_pump_speed_entity")),
            heat_carrier_pump_speed_entity=merged.get("heat_carrier_pump_speed_entity", existing.get("heat_carrier_pump_speed_entity")),
            compressor_activity_entity=merged.get("compressor_activity_entity", existing.get("compressor_activity_entity")),
            compressor_speed_entity=merged.get("compressor_speed_entity", existing.get("compressor_speed_entity")),
            compressor_target_speed_entity=merged.get("compressor_target_speed_entity", existing.get("compressor_target_speed_entity")),
            dhw_target_temperature_entity=merged.get("dhw_target_temperature_entity", existing.get("dhw_target_temperature_entity")),
            controllable=merged.get("controllable", existing.get("controllable", False)),
            control_permission=merged.get("control_permission", existing.get("control_permission", False)),
            control_dual_permission_armed=merged.get("control_dual_permission_armed", False),
            actuator_type=merged.get("actuator_type", existing.get("actuator_type")),
            control_entity=merged.get("control_entity", existing.get("control_entity")),
            control_service=merged.get("control_service", existing.get("control_service")),
            control_min_power_w=merged.get("control_min_power_w", existing.get("control_min_power_w")),
            control_max_power_w=merged.get("control_max_power_w", existing.get("control_max_power_w")),
            control_hub=merged.get("control_hub", existing.get("control_hub")),
            control_elwa_ip=merged.get("control_elwa_ip", existing.get("control_elwa_ip")),
            control_unit=merged.get("control_unit", existing.get("control_unit")),
            control_address=merged.get("control_address", existing.get("control_address")),
            control_boiler_temperature_entity=merged.get("control_boiler_temperature_entity", existing.get("control_boiler_temperature_entity")),
            control_element_temperature_entity=merged.get("control_element_temperature_entity", existing.get("control_element_temperature_entity")),
            control_surplus_entity=merged.get("control_surplus_entity", existing.get("control_surplus_entity")),
            control_lockout_entity=merged.get("control_lockout_entity", existing.get("control_lockout_entity")),
            control_stop_temperature_c=merged.get("control_stop_temperature_c", existing.get("control_stop_temperature_c")),
            control_restart_temperature_c=merged.get("control_restart_temperature_c", existing.get("control_restart_temperature_c")),
            device_profile=merged.get("device_profile", existing.get("device_profile")),
            control_solar_start_threshold_w=merged.get("control_solar_start_threshold_w", existing.get("control_solar_start_threshold_w")),
            control_solar_factor=merged.get("control_solar_factor", existing.get("control_solar_factor")),
            control_solar_export_reserve_w=merged.get("control_solar_export_reserve_w", existing.get("control_solar_export_reserve_w")),
            control_element_taper_start_c=merged.get("control_element_taper_start_c", existing.get("control_element_taper_start_c")),
            control_element_hard_stop_c=merged.get("control_element_hard_stop_c", existing.get("control_element_hard_stop_c")),
            control_grid_backup_start_c=merged.get("control_grid_backup_start_c", existing.get("control_grid_backup_start_c")),
            control_grid_backup_stop_c=merged.get("control_grid_backup_stop_c", existing.get("control_grid_backup_stop_c")),
            control_keepalive_interval_s=merged.get("control_keepalive_interval_s", existing.get("control_keepalive_interval_s")),
            control_owner=merged.get("control_owner", existing.get("control_owner", "home_assistant")),
            control_previous_controller_entity=merged.get("control_previous_controller_entity", existing.get("control_previous_controller_entity")),
            control_handover_confirmed=merged.get("control_handover_confirmed", existing.get("control_handover_confirmed", False)),
            control_execution_arm_requested=merged.get("control_execution_arm_requested", existing.get("control_execution_arm_requested", False)),
            control_execution_arm_confirmed=merged.get("control_execution_arm_confirmed", existing.get("control_execution_arm_confirmed", False)),
            control_execution_master_enabled=merged.get("control_execution_master_enabled", existing.get("control_execution_master_enabled", False)),
            control_emergency_stop=merged.get("control_emergency_stop", existing.get("control_emergency_stop", False)),
            control_goe_id=merged.get("control_goe_id", existing.get("control_goe_id")),
            control_mqtt_topic=merged.get("control_mqtt_topic", existing.get("control_mqtt_topic")),
            control_grid_power_entity=merged.get("control_grid_power_entity", existing.get("control_grid_power_entity")),
            control_battery_power_entity=merged.get("control_battery_power_entity", existing.get("control_battery_power_entity")),
            control_publish_interval_s=merged.get("control_publish_interval_s", existing.get("control_publish_interval_s")),
        )
        issues = await core.registry.async_add_device(device)
        core.device_import_manager.last_validation = {
            "status": "Updated" if not any(i["severity"] == "error" for i in issues) else "Error",
            "device": device,
            "issues": issues,
            "message": "Device update completed with persistence-safe field merge.",
        }
        core.update_engine.refresh_tracked_entities()
        core.refresh_pipeline()
        await core.smart_control.async_evaluate_execution()

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
            result["battery_sign_convention"] = call.data.get("battery_sign_convention", "unsigned_magnitude")
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
        import json
        core = _core(hass)
        tariff_mode = str(call.data.get("tariff_mode", "fixed") or "fixed").strip().lower()
        if tariff_mode not in {"fixed", "time_of_use"}:
            raise vol.Invalid("tariff_mode must be fixed or time_of_use")
        import_tariff = float(call.data.get("import_tariff", 0.0))
        export_tariff = float(call.data["export_tariff"])
        standing_charge = float(call.data.get("standing_charge", 0.0))
        if min(import_tariff, export_tariff, standing_charge) < 0:
            raise vol.Invalid("Tariff values cannot be negative")
        tou_periods = []
        if tariff_mode == "time_of_use":
            try:
                raw_periods = json.loads(str(call.data.get("tou_periods", "[]") or "[]"))
            except Exception as err:
                raise vol.Invalid("Invalid time-of-use period JSON") from err
            if not isinstance(raw_periods, list) or not raw_periods:
                raise vol.Invalid("At least one time-of-use tariff period is required")
            covered = [False] * 1440
            for idx, raw in enumerate(raw_periods):
                if not isinstance(raw, dict):
                    raise vol.Invalid("Each tariff period must be an object")
                name = str(raw.get("name") or f"Period {idx+1}").strip()[:40]
                start = str(raw.get("start") or "").strip()
                end = str(raw.get("end") or "").strip()
                try:
                    sh, sm = [int(x) for x in start.split(":", 1)]
                    eh, em = [int(x) for x in end.split(":", 1)]
                except Exception as err:
                    raise vol.Invalid(f"Invalid time in tariff period {idx+1}") from err
                if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
                    raise vol.Invalid(f"Invalid time in tariff period {idx+1}")
                start_min, end_min = sh * 60 + sm, eh * 60 + em
                if start_min == end_min:
                    raise vol.Invalid(f"Tariff period {idx+1} cannot have the same start and end time")
                rate = float(raw.get("import_tariff", 0.0))
                if rate < 0:
                    raise vol.Invalid("Tariff values cannot be negative")
                minutes = list(range(start_min, end_min)) if start_min < end_min else list(range(start_min, 1440)) + list(range(0, end_min))
                if any(covered[m] for m in minutes):
                    raise vol.Invalid("Time-of-use tariff periods cannot overlap")
                for m in minutes:
                    covered[m] = True
                tou_periods.append({"id": str(raw.get("id") or f"period_{idx+1}"), "name": name, "start": f"{sh:02d}:{sm:02d}", "end": f"{eh:02d}:{em:02d}", "import_tariff": rate})
            if not all(covered):
                raise vol.Invalid("Time-of-use tariff periods must cover the full 24-hour day")
            # Keep a compatibility import rate for older Zeus surfaces. The live
            # Finance engine exposes the actual active/effective rate separately.
            import_tariff = sum(float(x["import_tariff"]) * (((int(x["end"][:2])*60+int(x["end"][3:])) - (int(x["start"][:2])*60+int(x["start"][3:]))) % 1440) for x in tou_periods) / 1440.0
        core.registry.data.setdefault("sources", {})["tariffs"] = {
            "enabled": True, "currency": str(call.data.get("currency", "CHF")).upper(),
            "tariff_mode": tariff_mode, "import_tariff": import_tariff, "export_tariff": export_tariff,
            "tou_periods": tou_periods,
            "standing_charge": standing_charge, "vat_included": bool(call.data.get("vat_included", True)),
            "saved_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        core.registry.data.setdefault("audit", []).append({"action": "save_tariff_settings"})
        await core.registry.async_save()
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)

    async def clear_tariff_settings(call: ServiceCall) -> None:
        core = _core(hass)
        core.registry.data.setdefault("sources", {})["tariffs"] = {"enabled": False, "currency": "CHF", "tariff_mode": "fixed", "import_tariff": None, "export_tariff": None, "tou_periods": [], "standing_charge": 0.0, "vat_included": True}
        core.registry.data.setdefault("audit", []).append({"action": "clear_tariff_settings"})
        await core.registry.async_save()
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)

    async def set_energy_prices(call: ServiceCall) -> None:
        """Import an absolute dynamic import-price schedule from a HA automation."""
        from datetime import datetime, timezone

        core = _core(hass)
        raw_prices = list(call.data.get("prices") or [])
        if not raw_prices:
            raise vol.Invalid("prices must contain at least one start/end/price slot")
        if len(raw_prices) > 400:
            raise vol.Invalid("prices contains too many slots (maximum 400)")

        currency = str(call.data.get("currency") or "EUR").strip().upper()[:4]
        price_unit = str(call.data.get("price_unit") or "MWh").strip().lower().replace(" ", "")
        aliases = {"mwh": "MWh", "eur/mwh": "MWh", "chf/mwh": "MWh", "kwh": "kWh", "eur/kwh": "kWh", "chf/kwh": "kWh", "wh": "Wh"}
        unit = aliases.get(price_unit)
        if unit is None:
            raise vol.Invalid("price_unit must be MWh, kWh or Wh")
        factor = 0.001 if unit == "MWh" else (1000.0 if unit == "Wh" else 1.0)

        slots = []
        for idx, raw in enumerate(raw_prices):
            if not isinstance(raw, dict):
                raise vol.Invalid(f"price slot {idx + 1} must be an object")
            try:
                start = datetime.fromisoformat(str(raw.get("start") or "").replace("Z", "+00:00"))
                end = datetime.fromisoformat(str(raw.get("end") or "").replace("Z", "+00:00"))
                price = float(raw.get("price"))
            except (TypeError, ValueError) as err:
                raise vol.Invalid(f"price slot {idx + 1} has invalid start/end/price") from err
            if start.tzinfo is None or end.tzinfo is None:
                raise vol.Invalid(f"price slot {idx + 1} timestamps must include a timezone offset")
            if end <= start:
                raise vol.Invalid(f"price slot {idx + 1} end must be after start")
            slots.append({
                "start": start.astimezone(timezone.utc).isoformat(),
                "end": end.astimezone(timezone.utc).isoformat(),
                "price": price,
                "price_per_kwh": round(price * factor, 9),
            })
        slots.sort(key=lambda item: item["start"])
        for previous, current in zip(slots, slots[1:]):
            if current["start"] < previous["end"]:
                raise vol.Invalid("dynamic tariff slots cannot overlap")

        now = datetime.now(timezone.utc)
        source = str(call.data.get("source") or "Home Assistant automation").strip()[:80]
        tariff = dict(core.registry.data.setdefault("sources", {}).get("tariffs") or {})
        tariff.update({
            "enabled": True,
            "currency": currency,
            "tariff_mode": "dynamic",
            "dynamic_prices": slots,
            "dynamic_source": source,
            "dynamic_input_unit": unit,
            "dynamic_received_at": now.isoformat(),
            "dynamic_coverage_start": slots[0]["start"],
            "dynamic_coverage_end": slots[-1]["end"],
            "tou_periods": [],
        })
        # Preserve fixed export/standing-charge settings. Dynamic data controls only import prices.
        tariff.setdefault("export_tariff", 0.0)
        tariff.setdefault("standing_charge", 0.0)
        tariff.setdefault("vat_included", True)
        tariff["import_tariff"] = next((x["price_per_kwh"] for x in slots if x["start"] <= now.isoformat() < x["end"]), slots[0]["price_per_kwh"])
        core.registry.data.setdefault("sources", {})["tariffs"] = tariff
        core.registry.data.setdefault("audit", []).append({"action": "set_energy_prices", "source": source, "slot_count": len(slots), "currency": currency, "price_unit": unit})
        await core.registry.async_save()
        core.refresh_pipeline()
        await _refresh_aion_entities(hass)

    async def clear_dynamic_tariff(call: ServiceCall) -> None:
        core = _core(hass)
        tariff = dict(core.registry.data.setdefault("sources", {}).get("tariffs") or {})
        for key in ("dynamic_prices", "dynamic_source", "dynamic_input_unit", "dynamic_received_at", "dynamic_coverage_start", "dynamic_coverage_end"):
            tariff.pop(key, None)
        tariff["tariff_mode"] = "fixed"
        tariff["enabled"] = bool(tariff.get("import_tariff") is not None or tariff.get("export_tariff") is not None)
        core.registry.data.setdefault("sources", {})["tariffs"] = tariff
        core.registry.data.setdefault("audit", []).append({"action": "clear_dynamic_tariff"})
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
            battery_convention = call.data.get("battery_sign_convention", "unsigned_magnitude")
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
    mapping_schema = vol.Schema({vol.Required("field"): vol.In(list(_core(hass).energy_mapping.FIELD_RULES)), vol.Required("entity_id"): cv.entity_id, vol.Optional("sign_convention"): vol.In(["positive_import", "positive_export"]), vol.Optional("grid_mode"): vol.In(["bidirectional", "separate"]), vol.Optional("battery_mode"): vol.In(["bidirectional", "separate"]), vol.Optional("battery_sign_convention"): vol.In(["positive_discharge", "positive_charge", "unsigned_magnitude"])})
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
            vol.Optional("tariff_mode", default="fixed"): cv.string,
            vol.Optional("import_tariff", default=0.0): vol.Coerce(float),
            vol.Required("export_tariff"): vol.Coerce(float),
            vol.Optional("tou_periods", default="[]"): cv.string,
            vol.Optional("standing_charge", default=0.0): vol.Coerce(float),
            vol.Optional("vat_included", default=True): cv.boolean,
        }),
    )
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_TARIFF_SETTINGS,
    SERVICE_SAVE_BATTERY_CAPACITY,
    SERVICE_SAVE_HOME_PROFILE,
    SERVICE_CLEAR_BATTERY_CAPACITY, clear_tariff_settings)
    dynamic_price_schema = vol.Schema({
        vol.Required("prices"): vol.All(cv.ensure_list, [vol.Schema({
            vol.Required("start"): cv.string,
            vol.Required("end"): cv.string,
            vol.Required("price"): vol.Coerce(float),
        }, extra=vol.ALLOW_EXTRA)]),
        vol.Optional("currency", default="EUR"): cv.string,
        vol.Optional("price_unit", default="MWh"): cv.string,
        vol.Optional("source", default="Home Assistant automation"): cv.string,
    })
    hass.services.async_register(DOMAIN, SERVICE_SET_ENERGY_PRICES, set_energy_prices, schema=dynamic_price_schema)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_DYNAMIC_TARIFF, clear_dynamic_tariff)

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
            vol.Optional("tariff_mode", default="fixed"): cv.string,
            vol.Optional("import_tariff", default=0.0): vol.Coerce(float),
            vol.Required("export_tariff"): vol.Coerce(float),
            vol.Optional("tou_periods", default="[]"): cv.string,
            vol.Optional("standing_charge", default=0.0): vol.Coerce(float),
            vol.Optional("vat_included", default=True): cv.boolean,
        }))
    if not hass.services.has_service(DOMAIN, SERVICE_CLEAR_TARIFF_SETTINGS):
        hass.services.async_register(DOMAIN, SERVICE_CLEAR_TARIFF_SETTINGS,
    SERVICE_SAVE_BATTERY_CAPACITY,
    SERVICE_SAVE_HOME_PROFILE,
    SERVICE_CLEAR_BATTERY_CAPACITY, clear_tariff_settings)
    if not hass.services.has_service(DOMAIN, SERVICE_SET_ENERGY_PRICES):
        hass.services.async_register(DOMAIN, SERVICE_SET_ENERGY_PRICES, set_energy_prices, schema=dynamic_price_schema)
    if not hass.services.has_service(DOMAIN, SERVICE_CLEAR_DYNAMIC_TARIFF):
        hass.services.async_register(DOMAIN, SERVICE_CLEAR_DYNAMIC_TARIFF, clear_dynamic_tariff)


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

    async def save_switch_hub_device(call: ServiceCall) -> None:
        core = _core(hass)
        rows = list(core.registry.data.get("switch_hub") or [])
        device_id = str(call.data["device_id"]).strip()
        row = {
            "id": device_id,
            "name": str(call.data.get("name") or device_id).strip()[:120],
            "switch_entity": str(call.data["switch_entity"]).strip(),
            "power_entity": str(call.data.get("power_entity") or "").strip(),
            "control_enabled": bool(call.data.get("control_enabled", False)),
            "trigger_mode": str(call.data.get("trigger_mode") or "surplus"),
            "solar_surplus_w": max(1, int(call.data.get("solar_surplus_w", 1000))),
            "on_time": str(call.data.get("on_time") or "22:00"),
            "off_time": str(call.data.get("off_time") or "06:00"),
        }
        replaced = False
        for index, existing in enumerate(rows):
            if str(existing.get("id") or "") == device_id:
                rows[index] = row
                replaced = True
                break
        if not replaced:
            rows.append(row)
        core.registry.data["switch_hub"] = rows[:30]
        await core.registry.async_save()
        await core.switch_hub.async_evaluate()
        await _refresh_aion_entities(hass)

    async def remove_switch_hub_device(call: ServiceCall) -> None:
        core = _core(hass)
        device_id = str(call.data["device_id"]).strip()
        core.registry.data["switch_hub"] = [
            row for row in (core.registry.data.get("switch_hub") or [])
            if str(row.get("id") or "") != device_id
        ]
        await core.registry.async_save()
        await core.switch_hub.async_evaluate()
        await _refresh_aion_entities(hass)

    hass.services.async_register(DOMAIN, SERVICE_SAVE_SWITCH_HUB_DEVICE, save_switch_hub_device, schema=vol.Schema({
        vol.Required("device_id"): cv.string,
        vol.Required("name"): cv.string,
        vol.Required("switch_entity"): cv.entity_id,
        vol.Optional("power_entity", default=""): vol.Any("", cv.entity_id),
        vol.Optional("control_enabled", default=False): cv.boolean,
        vol.Optional("trigger_mode", default="surplus"): vol.In(["surplus", "time"]),
        vol.Optional("solar_surplus_w", default=1000): vol.All(vol.Coerce(int), vol.Range(min=1, max=50000)),
        vol.Optional("on_time", default="22:00"): cv.string,
        vol.Optional("off_time", default="06:00"): cv.string,
    }))
    hass.services.async_register(DOMAIN, SERVICE_REMOVE_SWITCH_HUB_DEVICE, remove_switch_hub_device, schema=vol.Schema({
        vol.Required("device_id"): cv.string,
    }))

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
        SERVICE_SAVE_SWITCH_HUB_DEVICE, SERVICE_REMOVE_SWITCH_HUB_DEVICE,
        SERVICE_LIFECYCLE_STATUS,
        SERVICE_PREVIEW_HA_ENERGY_IMPORT, SERVICE_APPLY_HA_ENERGY_IMPORT,
    ]
    for service in services:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    REGISTERED = False
