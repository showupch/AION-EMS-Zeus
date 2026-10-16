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
from .climate_schedule import ClimateScheduleManager
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
        # v16.0.162: interactive pages consume the cached DEA snapshot. A UI open
        # must not launch another multi-period Recorder workload. The engine
        # refreshes on the normal capture cadence and async_refresh() itself is
        # serialized/cached for explicit configuration refreshes.
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


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/solar_irradiance_learning"})
@websocket_api.async_response
async def _websocket_solar_irradiance_learning(hass, connection, msg) -> None:
    """Read-only seven-day synchronized local irradiance / PV power evidence.

    Both inputs are mapped authorities. Historical values are binned into
    half-hour intervals; no future prediction or learned correction is applied.
    """
    from datetime import timedelta
    from collections import defaultdict
    from homeassistant.components.recorder import get_instance, history
    from homeassistant.components.recorder.util import session_scope
    from homeassistant.util import dt as dt_util

    core = hass.data.get(DOMAIN, {}).get("core")
    if core is None:
        connection.send_error(msg["id"], "not_ready", "Zeus is not ready")
        return
    station = (core.registry.data.get("sources", {}) or {}).get("local_weather_station", {}) or {}
    irradiance_id = str((station.get("entities") or {}).get("solar_radiation") or "").strip()
    lux_id = str((station.get("entities") or {}).get("illuminance") or "").strip()
    solar_id = str(core.energy_mapping.mappings.get("solar_power") or "").strip()
    if not station.get("enabled") or not irradiance_id:
        connection.send_result(msg["id"], {"status":"unavailable","reason":"No registered on-site irradiance source","samples":0})
        return
    if not solar_id:
        connection.send_result(msg["id"], {"status":"unavailable","reason":"Solar Power source not registered in Zeus Energy Mapping","samples":0,"source_irradiance":irradiance_id,"irradiance_records":0,"solar_records":0,"paired_buckets":0})
        return
    if irradiance_id == solar_id:
        connection.send_result(msg["id"], {"status":"unavailable","reason":"Sources must be independent","samples":0})
        return
    now = dt_util.utcnow()
    start = now - timedelta(days=7)
    # Lux is optional; preserve irradiance/PV diagnostics on sites without it.
    lux_id = lux_id if lux_id not in (irradiance_id, solar_id) else ""
    ids = [irradiance_id, solar_id] + ([lux_id] if lux_id else [])

    def query():
        with session_scope(hass=hass, read_only=True) as session:
            return history.get_significant_states_with_session(
                hass, session, start, now, ids, None, False, False, False, True
            )
    try:
        raw = await get_instance(hass).async_add_executor_job(query)
    except Exception:
        connection.send_result(msg["id"], {"status":"unavailable","reason":"Recorder history request failed","samples":0})
        return

    bins = {}
    counters = {"irradiance": 0, "solar": 0, "lux": 0}
    accepted = {"irradiance": 0, "solar": 0, "lux": 0}
    source_units = {}
    for key, eid in (("irradiance", irradiance_id), ("solar", solar_id), ("lux", lux_id)):
        if not eid: continue
        current = hass.states.get(eid)
        source_units[key] = str(current.attributes.get("unit_of_measurement") or "").strip() if current else ""
        counters[key] = len((raw or {}).get(eid, []))
        for state in (raw or {}).get(eid, []):
            try:
                value = float(state.state)
                unit = str(state.attributes.get("unit_of_measurement") or source_units[key]).strip()
                if key == "irradiance":
                    if unit not in ("W/m²", "W/m2"): continue
                elif key == "lux":
                    if unit.lower() not in ("lx", "lux"): continue
                elif unit == "kW":
                    value *= 1000
                elif unit != "W":
                    continue
                if not 0 <= value < 100000:
                    continue
                stamp = getattr(state, "last_updated", None) or getattr(state, "last_changed", None)
                if stamp is None: continue
                utc = dt_util.as_utc(stamp)
                bucket = utc.replace(minute=(utc.minute//30)*30,second=0,microsecond=0)
                bins.setdefault(bucket, {}).setdefault(key, []).append(value)
                accepted[key] += 1
            except (TypeError, ValueError, OverflowError):
                continue
    paired_buckets = sum(bool(r.get("irradiance") and r.get("solar")) for r in bins.values())
    paired_lux_buckets = sum(bool(r.get("irradiance") and r.get("solar") and r.get("lux")) for r in bins.values())
    samples = []
    for stamp, row in sorted(bins.items()):
        if not row.get("irradiance") or not row.get("solar"): continue
        irr = sum(row["irradiance"])/len(row["irradiance"])
        pv = sum(row["solar"])/len(row["solar"])
        if irr < 50: continue
        local = dt_util.as_local(stamp)
        lux = sum(row["lux"])/len(row["lux"]) if row.get("lux") else None
        samples.append({"time":stamp.isoformat(),"hour":local.hour+local.minute/60,
                        "irradiance_w_m2":round(irr,1),"solar_power_w":round(pv,1),
                        "illuminance_lx":round(lux,1) if lux is not None else None})
    morning = [r for r in samples if r["hour"] < 12]
    afternoon = [r for r in samples if r["hour"] >= 12]
    def summary(rows):
        return {"samples":len(rows),
                "mean_irradiance_w_m2":round(sum(r["irradiance_w_m2"] for r in rows)/len(rows),1) if rows else None,
                "mean_solar_power_kw":round(sum(r["solar_power_w"] for r in rows)/len(rows)/1000,2) if rows else None}
    lux_samples = [r for r in samples if r["illuminance_lx"] is not None]
    def lux_summary(rows):
        return {"samples":len(rows),
                "mean_illuminance_lx":round(sum(r["illuminance_lx"] for r in rows)/len(rows),1) if rows else None,
                "mean_lux_per_w_m2":round(sum(r["illuminance_lx"]/r["irradiance_w_m2"] for r in rows)/len(rows),1) if rows else None}
    connection.send_result(msg["id"],{
        "status":"ready" if samples else "unavailable",
        "reason":None if samples else ("No recorded solar power samples" if not counters["solar"] else "No recorded irradiance samples" if not counters["irradiance"] else "No synchronized daylight Recorder samples"),
        "period":"Last 7 days", "samples":len(samples),
        "source_irradiance":irradiance_id,"source_solar":solar_id,"source_lux":lux_id or None,
        "irradiance_records":counters["irradiance"],"solar_records":counters["solar"],
        "irradiance_accepted":accepted["irradiance"],"solar_accepted":accepted["solar"],
        "paired_buckets":paired_buckets,
        "lux_records":counters["lux"],"lux_accepted":accepted["lux"],
        "paired_lux_buckets":paired_lux_buckets,"paired_daylight_lux_intervals":len(lux_samples),
        "lux":lux_summary(lux_samples),
        "lux_morning":lux_summary([r for r in lux_samples if r["hour"] < 12]),
        "lux_afternoon":lux_summary([r for r in lux_samples if r["hour"] >= 12]),
        "morning":summary(morning),"afternoon":summary(afternoon),
        "points":samples[-336:],
        "note":"Read-only paired 30-minute historical samples; not a forecast or physical conversion model."
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
    # Day Status must support both the simple whole-unit Heat Pump mapping and
    # the explicit circuit mappings used by installations that expose Heating /
    # DHW / Cooling independently.  Keep every source identified; the frontend
    # aggregates circuit power only when the whole-unit source is not mapped.
    keys = {
        "power":"power_entity", "thermal":"thermal_power_entity", "cop":"cop_entity",
        "flow":"supply_temperature_entity", "dhw":"dhw_temperature_entity",
        "outside":"outdoor_temperature_entity", "compressor":"compressor_state_entity",
        "heating_electrical":"heating_electrical_power_entity",
        "heating_thermal":"heating_thermal_power_entity",
        "dhw_electrical":"dhw_electrical_power_entity",
        "dhw_thermal":"dhw_thermal_power_entity",
        "cooling_electrical":"cooling_electrical_power_entity",
        "cooling_thermal":"cooling_thermal_power_entity",
    }
    maps = {k:str(d.get(v) or "").strip() for k,v in keys.items()}
    maps = {k:v for k,v in maps.items() if v}
    now=dt_util.now(); start=dt_util.as_utc(dt_util.start_of_local_day(now)); end=dt_util.as_utc(now+timedelta(minutes=1))
    ids=list(dict.fromkeys(maps.values()))
    def query():
        if not ids: return {}
        with session_scope(hass=hass, read_only=True) as session:
            return history.get_significant_states_with_session(hass,session,start,end,ids,None,False,False,False,True)
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



@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/heat_pump_monthly_energy"})
@websocket_api.async_response
async def _websocket_heat_pump_monthly_energy(hass, connection, msg) -> None:
    """Monthly whole-unit HP electrical energy from Recorder statistics only."""
    from datetime import datetime, timedelta
    from calendar import monthrange
    from homeassistant.util import dt as dt_util
    from .period_authority import trusted_data_epoch

    core = hass.data.get(DOMAIN, {}).get("core")
    if core is None:
        connection.send_error(msg["id"], "not_ready", "AION EMS is not ready")
        return
    now = dt_util.now()
    # Only a configured accounting boundary may override the calendar year.
    settings = core.registry.data.get("home_settings", {}) or {}
    # Prefer explicit accounting-year configuration. Older Zeus installations
    # store the accounting start as the trusted data epoch (e.g. 01 September)
    # without separate accounting_year_start_* fields. Use that existing
    # boundary rather than silently reverting the monthly table to January.
    epoch = trusted_data_epoch()
    start_month = settings.get("accounting_year_start_month")
    start_day = settings.get("accounting_year_start_day")
    if start_month in (None, ""):
        start_month = epoch.month if epoch is not None else 1
    if start_day in (None, ""):
        start_day = epoch.day if epoch is not None else 1
    try:
        start_month, start_day = int(start_month), int(start_day)
        if not 1 <= start_month <= 12 or not 1 <= start_day <= 28:
            raise ValueError("Invalid accounting boundary")
    except (TypeError, ValueError):
        connection.send_error(msg["id"], "invalid_accounting_year", "Configured accounting year boundary is invalid")
        return
    start_year = now.year if (now.month, now.day) >= (start_month, start_day) else now.year - 1
    start_local = datetime(start_year, start_month, start_day, tzinfo=dt_util.get_default_time_zone())
    boundaries = [start_local]
    for _ in range(12):
        prev = boundaries[-1]
        y = prev.year + (prev.month == 12)
        m = prev.month % 12 + 1
        boundaries.append(datetime(y, m, min(start_day, monthrange(y, m)[1]), tzinfo=prev.tzinfo))
    epoch = trusted_data_epoch()
    devices = [d for d in core.registry.data.get("devices", []) if str(d.get("type") or "") == "heat_pump"]
    # Whole-unit counter is authoritative; otherwise use exactly two distinct
    # classified electrical circuit counters for split heat pumps.
    ids = []
    source_status = []
    for d in devices:
        whole = str(d.get("energy_entity") or "").strip()
        if whole:
            ids.append(whole)
            source_status.append("whole_unit")
            continue
        heating = str(d.get("heating_electrical_energy_entity") or "").strip()
        dhw = str(d.get("dhw_electrical_energy_entity") or "").strip()
        if d.get("separate_heating_dhw_measurements") is True and heating and dhw and heating != dhw:
            ids.extend((heating, dhw))
            source_status.append("split_heating_dhw")
        else:
            source_status.append("incomplete")
    rows = [{"month": boundaries[i].strftime("%Y-%m"), "label": boundaries[i].strftime("%b %Y"), "consumption_kwh": None,
             "self_sufficiency": None, "pv_share": None, "grid_share": None, "cost": None} for i in range(12)]
    if not ids or len(ids) != len(set(ids)) or "incomplete" in source_status:
        connection.send_result(msg["id"], {"rows": rows, "status": "Missing or overlapping electrical energy sources", "year_start": start_local.date().isoformat()})
        return
    for eid in ids:
        state = hass.states.get(eid)
        unit = str((state.attributes.get("unit_of_measurement") if state else "") or "")
        if unit not in ("kWh", "Wh", "MWh"):
            connection.send_result(msg["id"], {"rows": rows, "status": f"Invalid energy unit for {eid}", "year_start": start_local.date().isoformat()})
            return
    try:
        response = await hass.services.async_call("recorder", "get_statistics", {
            "statistic_ids": ids, "start_time": dt_util.as_utc(boundaries[0]),
            "end_time": dt_util.as_utc(min(now, boundaries[-1])), "period": "day", "types": ["change"],
            "units": {"energy": "kWh"}}, blocking=True, return_response=True)
    except Exception as err:
        connection.send_error(msg["id"], "recorder_query_failed", str(err))
        return
    raw = (response or {}).get("statistics", response or {})
    for i in range(12):
        if boundaries[i] > now:
            continue
        start = max(dt_util.as_utc(boundaries[i]), epoch) if epoch else dt_util.as_utc(boundaries[i])
        end = min(dt_util.as_utc(boundaries[i+1]), dt_util.as_utc(now))
        if start >= end:
            continue
        total = 0.0
        found = False
        found_ids = set()
        for eid in ids:
            for point in (raw or {}).get(eid, []) or []:
                try:
                    at = point.get("start")
                    if isinstance(at, (int, float)):
                        at = dt_util.utc_from_timestamp(at)
                    elif isinstance(at, str):
                        at = dt_util.parse_datetime(at)
                    if at is None or not start <= dt_util.as_utc(at) < end:
                        continue
                    change = float(point["change"])
                    if change < 0:
                        continue
                    total += change
                    found = True
                    found_ids.add(eid)
                except (ValueError, TypeError, KeyError):
                    continue
        if found and len(found_ids) == len(ids):
            rows[i]["consumption_kwh"] = round(total, 3)
    # Monthly consumption must be returned independently of optional attribution.
    # This guard protects the working Recorder table from optional mapping errors.
    values = [r["consumption_kwh"] for r in rows if r["consumption_kwh"] is not None]
    total_consumption = sum(values)
    from .heat_pump_source_allocation import allocate_month
    try:
        mapping = (core.energy_mapping.summary().get("mappings") or {})
    except Exception:
        mapping = {}
    hp_power = [str(d.get("power_entity") or "").strip() for d in devices]
    hp_power = [x for x in hp_power if x]
    source_ids = {
        "hp": hp_power[0] if len(hp_power) == 1 and len(devices) == 1 else "",
        "house": str(mapping.get("house_power") or mapping.get("home_power") or "").strip(),
        "solar": str(mapping.get("solar_power") or "").strip(),
        "import": str(mapping.get("grid_import_power") or "").strip(),
        "export": str(mapping.get("grid_export_power") or "").strip(),
        "charge": str(mapping.get("battery_charge_power") or "").strip(),
        "discharge": str(mapping.get("battery_discharge_power") or "").strip(),
    }
    required = list(source_ids.values())
    if all(required) and len(set(required)) == len(required):
        units = {}
        for key, entity_id in source_ids.items():
            state = hass.states.get(entity_id)
            units[key] = state.attributes.get("unit_of_measurement") if state else None
        if all(unit in ("W", "kW") for unit in units.values()):
            try:
                power_response = await hass.services.async_call("recorder", "get_statistics", {
                    "statistic_ids": required,
                    "start_time": dt_util.as_utc(boundaries[0]),
                    "end_time": dt_util.as_utc(min(now, boundaries[-1])),
                    "period": "hour", "types": ["mean"],
                    "units": {"power": "W"},
                }, blocking=True, return_response=True)
                power_data = (power_response or {}).get("statistics", power_response or {})
                # Recorder statistics requested in W regardless of entity units.
                normalized_units = {key: "W" for key in source_ids}
                for i, row in enumerate(rows):
                    if row["consumption_kwh"] is None or boundaries[i] > now:
                        continue
                    begin = dt_util.as_utc(boundaries[i])
                    finish = min(dt_util.as_utc(boundaries[i+1]), dt_util.as_utc(now))
                    # Only complete UTC hours; avoid claiming coverage for partial hours.
                    finish = finish.replace(minute=0, second=0, microsecond=0)
                    if finish <= begin:
                        continue
                    result = allocate_month(power_data, source_ids, normalized_units,
                                            begin, finish, row["consumption_kwh"])
                    if result:
                        row.update(result)
                        # This is a *modelled* tariff-equivalent amount, not a
                        # historical invoice. Only fixed tariff configurations
                        # qualify; TOU/dynamic rates require dated price history.
                        # Reuse the canonical Finance Engine, not a second
                        # independent tariff interpretation.
                        finance = core.finance.summary() or {}
                        tariff_cfg = (core.registry.data.get("sources", {}) or {}).get("tariffs", {}) or {}
                        mode = str(finance.get("tariff_mode") or tariff_cfg.get("tariff_mode") or "fixed").lower()
                        row["cost_status"] = "Finance tariff evidence unavailable"
                        if not finance.get("configured"):
                            row["cost_status"] = "Finance tariff not configured"
                        elif mode == "fixed":
                            try:
                                rate = float(finance.get("import_tariff"))
                                if 0 <= rate < 100:
                                    row["cost"] = round(result["allocated_grid_kwh"] * rate, 2)
                                    row["cost_method"] = "Modelled grid allocation at current fixed tariff; not historical billing"
                                    row["cost_status"] = "Fixed tariff allocation available"
                            except (TypeError, ValueError):
                                row["cost_status"] = "Canonical fixed import tariff unavailable"
                        elif mode in ("time_of_use", "dynamic"):
                            # Price each allocated grid kWh at the timestamp of
                            # its measured hourly allocation, not a monthly average.
                            from datetime import datetime
                            from math import isfinite
                            engine = core.finance
                            slots = engine._dynamic_slots(tariff_cfg) if mode == "dynamic" else []
                            periods = engine._tou_periods(tariff_cfg) if mode == "time_of_use" else []
                            prices = []
                            for timestamp, kwh in result.get("allocated_grid_hourly", []):
                                when = datetime.fromisoformat(timestamp)
                                if mode == "dynamic":
                                    rate, _ = engine._dynamic_rate_for_datetime(when, slots)
                                else:
                                    # Unlike _tou_rate_for_datetime, uncovered
                                    # hours are missing evidence, not a zero tariff.
                                    from homeassistant.util import dt as dt_util
                                    local = dt_util.as_local(when)
                                    minute = local.hour * 60 + local.minute
                                    matching = [period for period in periods if
                                        engine._period_matches(minute, period["start_minute"], period["end_minute"])]
                                    rate = matching[0]["import_tariff"] if len(matching) == 1 else None
                                try:
                                    rate = float(rate) if rate is not None else None
                                except (TypeError, ValueError):
                                    rate = None
                                if rate is None or not isfinite(rate) or not 0 <= rate < 100:
                                    prices = []
                                    break
                                prices.append((kwh, rate))
                            if prices and len(prices) == len(result.get("allocated_grid_hourly", [])):
                                row["cost"] = round(sum(kwh * rate for kwh, rate in prices), 2)
                                row["cost_method"] = f"Modelled hourly grid allocation × {mode} tariff schedule; not billed cost"
                                row["cost_status"] = "Timestamped tariff evidence matched"
                            else:
                                row["cost_status"] = f"Unavailable: {mode} tariff missing historical hourly coverage"
                        else:
                            row["cost_status"] = "Unknown tariff mode"
            except Exception:
                # The consumption evidence must remain usable if power LTS is absent.
                pass
    values = [r["consumption_kwh"] for r in rows if r["consumption_kwh"] is not None]
    attributed = [r for r in rows if r.get("grid_share") is not None]
    complete = bool(values) and len(attributed) == len(values) and total_consumption > 0
    totals = {}
    if complete:
        for field in ("pv_share", "grid_share", "self_sufficiency", "battery_share"):
            totals[field] = round(sum(r["consumption_kwh"] * r[field] for r in attributed) / total_consumption, 1)
        if all(r.get("cost") is not None for r in attributed):
            totals["cost"] = round(sum(r["cost"] for r in attributed), 2)
    # Always expose a reason when cost cannot be populated. No tariff
    # amount is invented and monthly consumption remains independent.
    cost_diagnostics = [
        {"month": r.get("label"), "reason": r.get("cost_status") or
         ("Allocation evidence unavailable" if r.get("grid_share") is None
          else "Canonical Finance tariff unavailable")}
        for r in rows if r.get("consumption_kwh") is not None and r.get("cost") is None
    ]
    connection.send_result(msg["id"], {"rows": rows, "totals": totals, "total_kwh": round(total_consumption, 3) if values else None,
        "year_start": start_local.date().isoformat(),
        "currency": str((core.registry.data.get("sources", {}) or {}).get("tariffs", {}).get("currency") or "CHF").upper()[:4],
        "cost_diagnostics": cost_diagnostics,
        "status": "Measured monthly Recorder consumption. Source shares are modelled, not directly metered. Cost uses the canonical Finance Engine fixed tariff when available, and is not historical billed cost."})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/battery_day_history"})
@websocket_api.async_response
async def _websocket_battery_day_history(hass, connection, msg) -> None:
    """Return today's Recorder-backed Battery SOC and power history."""
    from datetime import timedelta
    from homeassistant.components.recorder import get_instance, history
    from homeassistant.components.recorder.util import session_scope
    from homeassistant.util import dt as dt_util
    core = hass.data.get(DOMAIN, {}).get("core")
    if core is None:
        connection.send_error(msg["id"], "not_ready", "AION EMS is not ready"); return
    summary = core.energy_mapping.summary(); mappings = summary.get("mappings", {}) or {}; options = summary.get("mapping_options", {}) or {}
    ids_by_key = {"soc": str(mappings.get("battery_soc") or "").strip(), "power": str(mappings.get("battery_power") or "").strip(), "charge": str(mappings.get("battery_charge_power") or "").strip(), "discharge": str(mappings.get("battery_discharge_power") or "").strip()}
    ids = list(dict.fromkeys(v for v in ids_by_key.values() if v))
    if not ids:
        connection.send_result(msg["id"], {"series": [], "mappings": ids_by_key, "options": options, "status": "No canonical Battery mapping"}); return
    now = dt_util.now(); start = dt_util.as_utc(dt_util.start_of_local_day(now)); end = dt_util.as_utc(now + timedelta(minutes=1))
    def query():
        with session_scope(hass=hass, read_only=True) as session:
            return history.get_significant_states_with_session(hass, session, start, end, ids, None, True, False, False, True)
    try: raw = await get_instance(hass).async_add_executor_job(query)
    except Exception as err:
        connection.send_error(msg["id"], "recorder_query_failed", str(err)); return
    rows = []
    for key, eid in ids_by_key.items():
        if not eid: continue
        current = hass.states.get(eid); current_unit = current.attributes.get("unit_of_measurement") if current else None
        for st in list((raw or {}).get(eid, []) or []):
            stamp = getattr(st, "last_changed", None) or getattr(st, "last_updated", None)
            if stamp is None: continue
            try: value = float(st.state)
            except (TypeError, ValueError): continue
            unit = st.attributes.get("unit_of_measurement") or current_unit
            if key != "soc":
                if str(unit or "").lower() == "kw": value *= 1000.0
                elif str(unit or "").lower() == "mw": value *= 1000000.0
            rows.append({"key": key, "entity_id": eid, "at": dt_util.as_utc(stamp).isoformat(), "value": value, "unit": unit})
    rows.sort(key=lambda x: x["at"])
    connection.send_result(msg["id"], {"series": rows, "mappings": ids_by_key, "options": options, "source": "Home Assistant Recorder", "date": dt_util.as_local(start).date().isoformat()})


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
    house_id=str(mappings.get("house_power") or mappings.get("home_power") or "").strip()
    ids=list(dict.fromkeys(x for x in (grid_id,import_id,export_id,house_id) if x))
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
    connection.send_result(msg["id"],{"series":rows,"mappings":{"grid":grid_id,"import":import_id,"export":export_id,"house":house_id},"options":options,"source":"Home Assistant Recorder"})


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



@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/climate_schedules"})
@websocket_api.async_response
async def _websocket_climate_schedules(hass, connection, msg):
    manager = hass.data.get(DOMAIN, {}).get("climate_schedule_manager")
    connection.send_result(msg["id"], manager.schedules if manager else {})

@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/save_climate_schedule", vol.Required("entity_id"): str, vol.Required("enabled"): bool, vol.Required("days"): list, vol.Required("slots"): list})
@websocket_api.async_response
async def _websocket_save_climate_schedule(hass, connection, msg):
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Admin access required")
        return
    manager = hass.data.get(DOMAIN, {}).get("climate_schedule_manager")
    if manager is None:
        connection.send_error(msg["id"], "not_ready", "Climate scheduler unavailable")
        return
    try:
        result = await manager.async_save(msg["entity_id"], msg["enabled"], msg["days"], msg["slots"])
    except (ValueError, TypeError, OverflowError) as exc:
        connection.send_error(msg["id"], "invalid_schedule", str(exc))
        return
    connection.send_result(msg["id"], result)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AION EMS from a config entry."""
    core = AionCore(hass, entry)
    await core.async_setup()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = core
    hass.data[DOMAIN]["core"] = core

    manager = hass.data[DOMAIN].get("climate_schedule_manager")
    if manager is None:
        manager = ClimateScheduleManager(hass)
        hass.data[DOMAIN]["climate_schedule_manager"] = manager
        await manager.async_start()
    await async_setup_services(hass)
    if not hass.data.get(_WEBSOCKET_REGISTERED):
        websocket_api.async_register_command(hass, _websocket_climate_schedules)
        websocket_api.async_register_command(hass, _websocket_save_climate_schedule)
        websocket_api.async_register_command(hass, _websocket_device_energy_attribution)
        websocket_api.async_register_command(hass, _websocket_ha_energy_import_preview)
        websocket_api.async_register_command(hass, _websocket_export_configuration)
        websocket_api.async_register_command(hass, _websocket_restore_configuration)
        websocket_api.async_register_command(hass, _websocket_entity_authority_recorder_evidence)
        websocket_api.async_register_command(hass, _websocket_solar_irradiance_learning)
        websocket_api.async_register_command(hass, _websocket_heat_pump_day_history)
        websocket_api.async_register_command(hass, _websocket_heat_pump_monthly_energy)
        websocket_api.async_register_command(hass, _websocket_solar_day_history)
        websocket_api.async_register_command(hass, _websocket_grid_day_history)
        websocket_api.async_register_command(hass, _websocket_battery_day_history)
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

    manager = hass.data.get(DOMAIN, {}).pop("climate_schedule_manager", None)
    if manager:
        await manager.async_stop()
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
