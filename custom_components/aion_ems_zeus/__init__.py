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



@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/entity_authority_recorder_evidence",
    vol.Required("entity_ids"): [str],
})
@websocket_api.async_response
async def _websocket_entity_authority_recorder_evidence(hass, connection, msg) -> None:
    """Return cached exact Recorder presence for mapped authority entities.

    This deliberately avoids live history expansion.  Entity Authorities only
    needs to know whether Recorder contains evidence for the exact entity_id.
    The result is cached for 15 minutes so opening/rendering the page can never
    turn into a high-frequency Recorder workload.
    """
    import time
    from sqlalchemy import text
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.util import session_scope

    entity_ids = list(dict.fromkeys(
        str(x).strip() for x in msg.get("entity_ids", []) if str(x).strip()
    ))[:100]
    if not entity_ids:
        connection.send_result(msg["id"], {"evidence": {}, "cached": True})
        return

    domain_data = hass.data.setdefault(DOMAIN, {})
    cache = domain_data.setdefault("entity_authority_recorder_cache", {})
    now = time.monotonic()
    ttl = 15 * 60
    evidence = {}
    missing = []
    for entity_id in entity_ids:
        item = cache.get(entity_id)
        if isinstance(item, dict) and now - float(item.get("checked", 0)) < ttl:
            evidence[entity_id] = {
                "available": bool(item.get("available")),
                "cached": True,
                "cache_minutes": 15,
            }
        else:
            missing.append(entity_id)

    if missing:
        def query_exact_presence():
            found = set()
            # Recorder's states_meta/entity_id relationship is the stable source
            # of truth for raw state evidence and works for power as well as
            # energy sensors. Query in chunks to keep parameter lists bounded.
            with session_scope(hass=hass, read_only=True) as session:
                for offset in range(0, len(missing), 50):
                    chunk = missing[offset:offset + 50]
                    params = {f"e{i}": value for i, value in enumerate(chunk)}
                    placeholders = ",".join(f":e{i}" for i in range(len(chunk)))
                    sql = text(
                        "SELECT DISTINCT sm.entity_id "
                        "FROM states_meta sm "
                        "JOIN states s ON s.metadata_id = sm.metadata_id "
                        f"WHERE sm.entity_id IN ({placeholders})"
                    )
                    for row in session.execute(sql, params):
                        if row and row[0]:
                            found.add(str(row[0]))
            return found

        try:
            found = await get_instance(hass).async_add_executor_job(query_exact_presence)
        except Exception as err:
            # One failed verification request must terminate cleanly. The
            # frontend records the attempt and will not retry on every render.
            connection.send_error(msg["id"], "recorder_query_failed", str(err))
            return

        checked = time.monotonic()
        for entity_id in missing:
            available = entity_id in found
            cache[entity_id] = {"available": available, "checked": checked}
            evidence[entity_id] = {
                "available": available,
                "cached": False,
                "cache_minutes": 15,
            }

    connection.send_result(msg["id"], {
        "evidence": evidence,
        "cache_minutes": 15,
    })


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/heat_pump_day_history"})
@websocket_api.async_response
async def _websocket_heat_pump_day_history(hass, connection, msg) -> None:
    from datetime import timedelta
    from homeassistant.components.recorder import get_instance, history
    from homeassistant.components.recorder.util import session_scope
    from homeassistant.util import dt as dt_util
    core = hass.data.get(DOMAIN, {}).get("core")
    if core is None:
        connection.send_error(msg["id"], "not_ready", "AION EMS is not ready"); return
    devices = [d for d in core.registry.data.get("devices", []) if str(d.get("type") or "") == "heat_pump"]
    if not devices:
        connection.send_result(msg["id"], {"series": []}); return
    d = devices[0]
    keys = {"power":"power_entity","thermal":"thermal_power_entity","cop":"cop_entity",
            "flow":"supply_temperature_entity","dhw":"dhw_temperature_entity",
            "outside":"outdoor_temperature_entity","compressor":"compressor_state_entity"}
    maps = {k:str(d.get(v) or "").strip() for k,v in keys.items()}
    maps = {k:v for k,v in maps.items() if v}
    now=dt_util.now(); start=dt_util.as_utc(dt_util.start_of_local_day(now)); end=dt_util.as_utc(now+timedelta(minutes=1))
    ids=list(dict.fromkeys(maps.values()))
    def query():
        if not ids: return {}
        with session_scope(hass=hass, read_only=True) as session:
            return history.get_significant_states_with_session(hass,session,start,end,ids,None,True,False,False,True)
    try: raw=await get_instance(hass).async_add_executor_job(query)
    except Exception as err:
        connection.send_error(msg["id"],"recorder_query_failed",str(err)); return

    # Thermal history is queried separately. Home Assistant's minimal-response
    # optimization is useful for the mixed graph request above, but for some
    # numeric power sensors it can collapse intermediate values. Keep every
    # recorded state for the mapped Combined Thermal Power entity only.
    thermal_raw = {}
    thermal_id = maps.get("thermal")
    if thermal_id:
        def query_thermal():
            with session_scope(hass=hass, read_only=True) as session:
                return history.get_significant_states_with_session(
                    hass, session, start, end, [thermal_id], None,
                    False, False, False, True
                )
        try:
            thermal_raw = await get_instance(hass).async_add_executor_job(query_thermal)
        except Exception:
            thermal_raw = {}

    rows=[]
    current_units = {}
    for key,eid in maps.items():
        current_state = hass.states.get(eid)
        current_units[key] = current_state.attributes.get("unit_of_measurement") if current_state is not None else None
    for key,eid in maps.items():
        source = thermal_raw if key == "thermal" and (thermal_raw or {}).get(eid) else raw
        for st in list((source or {}).get(eid,[]) or []):
            stamp=getattr(st,"last_changed",None) or getattr(st,"last_updated",None)
            if stamp is None: continue
            try: value=float(st.state)
            except (TypeError,ValueError): value=None
            rows.append({"key":key,"at":dt_util.as_utc(stamp).isoformat(),"value":value,
                         "state":str(st.state),"unit":st.attributes.get("unit_of_measurement") or current_units.get(key)})
    rows.sort(key=lambda x:x["at"])
    connection.send_result(msg["id"],{"series":rows,"mappings":maps,"source":"Home Assistant Recorder","date":dt_util.as_local(start).date().isoformat()})



@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/solar_day_history"})
@websocket_api.async_response
async def _websocket_solar_day_history(hass, connection, msg) -> None:
    """Return today's Recorder-backed Solar power history from registered PV sources."""
    from datetime import timedelta
    from homeassistant.components.recorder import get_instance, history
    from homeassistant.components.recorder.util import session_scope
    from homeassistant.util import dt as dt_util

    core = hass.data.get(DOMAIN, {}).get("core")
    if core is None:
        connection.send_error(msg["id"], "not_ready", "AION EMS is not ready"); return
    mapping_summary = core.energy_mapping.summary()
    canonical_id = str((mapping_summary.get("mappings", {}) or {}).get("solar_power") or "").strip()
    ids = [canonical_id] if canonical_id else []
    if not ids:
        connection.send_result(msg["id"], {"series": [], "mappings": [], "status": "No canonical Inputs Solar power mapping"}); return

    now=dt_util.now(); start=dt_util.as_utc(dt_util.start_of_local_day(now)); end=dt_util.as_utc(now+timedelta(minutes=1))
    def query():
        with session_scope(hass=hass, read_only=True) as session:
            return history.get_significant_states_with_session(hass,session,start,end,ids,None,True,False,False,True)
    try: raw=await get_instance(hass).async_add_executor_job(query)
    except Exception as err:
        connection.send_error(msg["id"],"recorder_query_failed",str(err)); return

    rows=[]
    for eid in ids:
        current=hass.states.get(eid)
        current_unit=current.attributes.get("unit_of_measurement") if current is not None else None
        for st in list((raw or {}).get(eid,[]) or []):
            stamp=getattr(st,"last_changed",None) or getattr(st,"last_updated",None)
            if stamp is None: continue
            try: value=float(st.state)
            except (TypeError,ValueError): continue
            unit=st.attributes.get("unit_of_measurement") or current_unit
            if str(unit or "").lower()=="kw": value*=1000.0
            elif str(unit or "").lower()=="mw": value*=1000000.0
            rows.append({"entity_id":eid,"at":dt_util.as_utc(stamp).isoformat(),"value_w":max(0.0,value)})
    rows.sort(key=lambda x:x["at"])
    connection.send_result(msg["id"],{"series":rows,"mappings":ids,"source":"Home Assistant Recorder","date":dt_util.as_local(start).date().isoformat()})



@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/grid_day_history"})
@websocket_api.async_response
async def _websocket_grid_day_history(hass, connection, msg) -> None:
    """Return today's Recorder-backed Grid history from canonical Inputs mappings."""
    from datetime import timedelta
    from homeassistant.components.recorder import get_instance, history
    from homeassistant.components.recorder.util import session_scope
    from homeassistant.util import dt as dt_util

    core=hass.data.get(DOMAIN,{}).get("core")
    if core is None:
        connection.send_error(msg["id"],"not_ready","AION EMS is not ready"); return
    summary=core.energy_mapping.summary()
    mappings=summary.get("mappings",{}) or {}
    options=summary.get("mapping_options",{}) or {}
    grid_id=str(mappings.get("grid_power") or "").strip()
    import_id=str(mappings.get("grid_import_power") or "").strip()
    export_id=str(mappings.get("grid_export_power") or "").strip()
    ids=list(dict.fromkeys(x for x in (grid_id,import_id,export_id) if x))
    if not ids:
        connection.send_result(msg["id"],{"series":[],"mappings":{},"status":"No canonical Grid power mapping"}); return

    now=dt_util.now();start=dt_util.as_utc(dt_util.start_of_local_day(now));end=dt_util.as_utc(now+timedelta(minutes=1))
    def query():
        with session_scope(hass=hass,read_only=True) as session:
            return history.get_significant_states_with_session(hass,session,start,end,ids,None,True,False,False,True)
    try: raw=await get_instance(hass).async_add_executor_job(query)
    except Exception as err:
        connection.send_error(msg["id"],"recorder_query_failed",str(err));return

    rows=[]
    for eid in ids:
        current=hass.states.get(eid); current_unit=current.attributes.get("unit_of_measurement") if current else None
        for st in list((raw or {}).get(eid,[]) or []):
            stamp=getattr(st,"last_changed",None) or getattr(st,"last_updated",None)
            if stamp is None:continue
            try:value=float(st.state)
            except (TypeError,ValueError):continue
            unit=st.attributes.get("unit_of_measurement") or current_unit
            if str(unit or "").lower()=="kw":value*=1000
            elif str(unit or "").lower()=="mw":value*=1000000
            rows.append({"entity_id":eid,"at":dt_util.as_utc(stamp).isoformat(),"value_w":value})
    rows.sort(key=lambda x:x["at"])
    connection.send_result(msg["id"],{"series":rows,"mappings":{"grid":grid_id,"import":import_id,"export":export_id},"options":options,"source":"Home Assistant Recorder"})


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
        sidebar_icon="mdi:lightning-bolt",
        module_url=f"{_FRONTEND_URL}?v={version}&build=zeus-15-0-4-dynamic-tariffs-cache-safe",
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
        websocket_api.async_register_command(hass, _websocket_entity_authority_recorder_evidence)
        websocket_api.async_register_command(hass, _websocket_heat_pump_day_history)
        websocket_api.async_register_command(hass, _websocket_solar_day_history)
        websocket_api.async_register_command(hass, _websocket_grid_day_history)
        hass.data[_WEBSOCKET_REGISTERED] = True
    await _async_register_frontend(hass, core.version)
    core.event_bus.publish(
        "DeviceManagerFrontendRegistered",
        "AION EMS",
        {"panel": ENERGY_FLOW_PANEL_URL_PATH, "sidebar_hidden": False, "sidebar_title": "AION EMS Zeus", "settings_embedded": True, "module_url": _FRONTEND_URL, "version": core.version},
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Heavy Recorder/history, weather/network and intelligence warm-up must not
    # hold Home Assistant's config-entry startup open.  ConfigEntry background
    # tasks are excluded from HA startup blocking and are cancelled on unload.
    entry.async_create_background_task(
        hass,
        core.async_finish_setup(),
        f"{DOMAIN} background warm-up",
        eager_start=False,
    )
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
