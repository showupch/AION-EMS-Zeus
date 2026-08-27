"""AION EMS integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.components import websocket_api
import voluptuous as vol
from homeassistant.components.panel_custom import async_register_panel
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, ENERGY_FLOW_PANEL_URL_PATH, COMMAND_CENTER_PANEL_URL_PATH, PLATFORMS
from .core import AionCore
from .services import async_setup_services, async_unload_services
from .lifecycle import LifecycleManager

_FRONTEND_URL = f"/api/{DOMAIN}/frontend/device_manager.js"
_FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"
_WEBSOCKET_REGISTERED = f"{DOMAIN}_websocket_registered"


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/device_energy_attribution"})
@websocket_api.async_response
async def _websocket_device_energy_attribution(hass, connection, msg) -> None:
    """Return full DEA detail from runtime memory without Recorder attributes."""
    core = hass.data.get(DOMAIN, {}).get("core")
    if core is None:
        connection.send_error(msg["id"], "not_ready", "AION EMS is not ready")
        return
    try:
        # Full DEA detail is requested by interactive Zeus pages.  Refresh it
        # against the current Registry + Recorder evidence first so the websocket
        # never returns a stale startup snapshot after device/mapping changes.
        await core.device_analytics.async_refresh_recorder_energy()
        core.device_analytics.refresh()
        payload = await core.device_energy_attribution.async_refresh()
    except Exception as err:
        connection.send_error(msg["id"], "dea_refresh_failed", str(err))
        return
    connection.send_result(msg["id"], payload)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/ha_energy_import_preview"})
@websocket_api.async_response
async def _websocket_ha_energy_import_preview(hass, connection, msg) -> None:
    """Return a read-only Home Assistant Energy import preview."""
    core = hass.data.get(DOMAIN, {}).get("core")
    if core is None:
        connection.send_error(msg["id"], "not_ready", "AION EMS is not ready")
        return
    try:
        result = await core.ha_energy_import.async_preview()
    except Exception as err:
        connection.send_error(msg["id"], "ha_energy_import_failed", str(err))
        return
    connection.send_result(msg["id"], result)


def _configuration_backup_payload(core) -> dict:
    """Build a portable Zeus configuration backup without HA Recorder history."""
    import copy
    from datetime import datetime, timezone
    data = copy.deepcopy(core.registry.data)
    # Runtime/audit backup bookkeeping is intentionally not portable configuration.
    data.pop("audit", None)
    data.pop("backups", None)
    return {
        "format": "aion_ems_zeus_configuration",
        "schema_version": 1,
        "zeus_version": core.version,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "registry_schema_version": data.get("schema_version"),
        "configuration": data,
        "excludes": ["Home Assistant Recorder/history", "runtime caches", "audit log", "registry backup history"],
    }


def _entity_references(value, path="configuration"):
    """Yield likely Home Assistant entity references from portable config."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(child, str) and (key == "entity_id" or key.endswith("_entity")) and "." in child:
                yield child_path, child
            else:
                yield from _entity_references(child, child_path)
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            yield from _entity_references(child, f"{path}[{idx}]")


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/export_configuration"})
@websocket_api.async_response
async def _websocket_export_configuration(hass, connection, msg) -> None:
    core = hass.data.get(DOMAIN, {}).get("core")
    if core is None:
        connection.send_error(msg["id"], "not_ready", "AION EMS is not ready")
        return
    connection.send_result(msg["id"], _configuration_backup_payload(core))


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/restore_configuration",
    vol.Required("backup"): dict,
    vol.Optional("apply", default=False): bool,
})
@websocket_api.async_response
async def _websocket_restore_configuration(hass, connection, msg) -> None:
    import copy
    from .period_authority import configure_data_epoch
    core = hass.data.get(DOMAIN, {}).get("core")
    if core is None:
        connection.send_error(msg["id"], "not_ready", "AION EMS is not ready")
        return
    backup = msg.get("backup") or {}
    if backup.get("format") != "aion_ems_zeus_configuration" or int(backup.get("schema_version", 0)) != 1:
        connection.send_error(msg["id"], "invalid_backup", "Not a supported Zeus configuration backup")
        return
    config = backup.get("configuration")
    if not isinstance(config, dict) or not isinstance(config.get("devices", []), list) or not isinstance(config.get("entity_mappings", {}), dict):
        connection.send_error(msg["id"], "invalid_backup", "Backup configuration structure is incomplete")
        return
    missing = []
    refs = list(_entity_references(config))
    seen = set()
    for path, entity_id in refs:
        if entity_id in seen:
            continue
        seen.add(entity_id)
        if hass.states.get(entity_id) is None:
            missing.append({"entity_id": entity_id, "path": path})
    result = {
        "valid": True,
        "source_version": str(backup.get("zeus_version") or "unknown"),
        "target_version": core.version,
        "entity_references": len(seen),
        "missing_entities": missing,
        "missing_count": len(missing),
        "can_restore": True,
        "applied": False,
    }
    if not msg.get("apply"):
        connection.send_result(msg["id"], result)
        return
    # Preserve local backup/audit history while replacing portable configuration.
    old_backups = copy.deepcopy(core.registry.data.get("backups", []))
    old_audit = copy.deepcopy(core.registry.data.get("audit", []))
    restored = copy.deepcopy(config)
    restored["backups"] = old_backups
    restored["audit"] = old_audit + [{"action": "restore_portable_configuration", "source_version": result["source_version"], "missing_entities": len(missing)}]
    core.registry.data.clear()
    core.registry.data.update(restored)
    await core.registry.async_save()
    epoch = core.registry.data.get("home_settings", {}).get("data_epoch")
    configure_data_epoch(epoch)
    core.settings_api.refresh()
    core.energy_mapping.refresh()
    core.refresh_pipeline()
    # Refresh diagnostics so portability/readiness status immediately reflects this HA host.
    try:
        core.qa_diagnostics.refresh()
    except Exception:
        pass
    result["applied"] = True
    connection.send_result(msg["id"], result)


async def _async_register_frontend(hass: HomeAssistant, version: str) -> None:
    """Serve and register the Device Manager as a native Home Assistant panel."""
    if not hass.data.get(_FRONTEND_REGISTERED):
        frontend_file = Path(__file__).parent / "frontend" / "device_manager.js"
        await hass.http.async_register_static_paths(
            [StaticPathConfig(_FRONTEND_URL, str(frontend_file), False)]
        )
        hass.data[_FRONTEND_REGISTERED] = True

    await async_register_panel(
        hass,
        frontend_url_path=ENERGY_FLOW_PANEL_URL_PATH,
        webcomponent_name="aion-ems-zeus-dashboard",
        sidebar_title="AION EMS Zeus",
        sidebar_icon="mdi:home-lightning-bolt",
        module_url=f"{_FRONTEND_URL}?v={version}&build=zeus-14-5-13-heatpump-restart-pattern-intelligence",
        config={"version": version, "domain": DOMAIN},
        require_admin=False,
    )

    # Dedicated 24-inch command-center panel. It intentionally has no sidebar
    # title/icon so Home Assistant does not add a second normal navigation item.
    await async_register_panel(
        hass,
        frontend_url_path=COMMAND_CENTER_PANEL_URL_PATH,
        webcomponent_name="aion-ems-zeus-command-center",
        sidebar_title=None,
        sidebar_icon=None,
        module_url=f"{_FRONTEND_URL}?v={version}&build=zeus-89-command-center-full-viewport",
        config={"version": version, "domain": DOMAIN, "dedicated_kiosk": True},
        require_admin=False,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AION EMS from a config entry."""
    core = AionCore(hass, entry)
    await core.async_setup()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = core
    hass.data[DOMAIN]["core"] = core

    await async_setup_services(hass)
    if not hass.data.get(_WEBSOCKET_REGISTERED):
        websocket_api.async_register_command(hass, _websocket_device_energy_attribution)
        websocket_api.async_register_command(hass, _websocket_ha_energy_import_preview)
        websocket_api.async_register_command(hass, _websocket_export_configuration)
        websocket_api.async_register_command(hass, _websocket_restore_configuration)
        hass.data[_WEBSOCKET_REGISTERED] = True
    await _async_register_frontend(hass, core.version)
    core.event_bus.publish(
        "DeviceManagerFrontendRegistered",
        "AION EMS",
        {"panel": ENERGY_FLOW_PANEL_URL_PATH, "settings_embedded": True, "module_url": _FRONTEND_URL, "version": core.version},
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload AION EMS without deleting persistent user data."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    lifecycle = LifecycleManager(hass)
    lifecycle.remove_panel()
    await async_unload_services(hass)

    core = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if core:
        await core.async_unload()

    hass.data.get(DOMAIN, {}).pop("core", None)
    if not hass.data.get(DOMAIN):
        hass.data.pop(DOMAIN, None)

    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove all AION EMS artifacts when the integration is uninstalled."""
    lifecycle = LifecycleManager(hass)
    await lifecycle.async_full_cleanup(entry.entry_id)
