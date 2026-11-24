"""Read-only V3 forecast shadow: isolated from production Forecast V2."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from aiohttp import ClientTimeout
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from .const import DOMAIN
from .v3_shadow_arrays import array_day
from .v3_shadow_dc_observation import dc_power_observation
from .v3_shadow_dc_history import collect_sample, hourly_dc_energy
from .v3_shadow_hourly_comparison import compare_hourly_dc
from .v3_shadow_ghi_comparison import compare_ghi
from .v3_shadow_actual_audit import compare_completed_days
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

class V3ShadowSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Forecast V3 Shadow"
    _attr_icon = "mdi:weather-sunny-alert"
    _attr_unique_id = f"{DOMAIN}_forecast_v3_shadow"
    _attr_should_poll = False

    def __init__(self, coordinator, core):
        super().__init__(coordinator)
        self.core = core
        self.entity_id = f"sensor.aion_ems_zeus_forecast_v3_shadow"
        self._state = "awaiting_weather_capture"
        self._details = {}
        self._running = False
        self._snapshots = []
        self._store = None
        self._dc_store = None
        self._dc_samples = []
        self._dc_sample_busy = False
        self._dc_last_error = None
        self._actual_rows_cache = []
        self._actual_store = None

    @property
    def native_value(self):
        return self._state

    @property
    def extra_state_attributes(self):
        """Expose a bounded Recorder-safe summary, not full diagnostic histories.

        Full weather forecast snapshots and DC observations are retained in
        their existing HA Store files; this changes no sampling or V2 logic.
        """
        details = self._details
        attrs = {"mode": "read_only_shadow", "v2_unchanged": True,
                 "production_applied": False, "control_permission": False,
                 "accuracy_validated": False}
        # Small scalar metadata remains directly accessible to automations.
        for key, value in details.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                attrs[key] = value if not isinstance(value, str) else value[:240]
        # Preserve compact diagnostics without exposing multi-day arrays.
        for key in ("dc_observation", "orientation_sunny_test",
                    "meteoswiss_comparison", "actual_comparison",
                    "orientation_diagnostics", "weather_rejection"):
            value = details.get(key)
            if isinstance(value, dict):
                attrs[key] = {
                    k: (v[:240] if isinstance(v, str) else v)
                    for k, v in value.items()
                    if v is None or isinstance(v, (str, int, float, bool))
                }
                if key == "actual_comparison":
                    for count_key in ("snapshot_count_checked", "authoritative_actual_days",
                                      "eligible_forecasts_without_authoritative_actual",
                                      "authoritative_actuals_without_eligible_forecast",
                                      "non_authoritative_actual_rows", "actual_rows_missing_source",
                                      "fresh_authoritative_days", "cached_authoritative_days"):
                        if isinstance(value.get(count_key), int):
                            attrs["actual_" + count_key] = value[count_key]
                    counts = value.get("snapshot_rejection_counts")
                    if isinstance(counts, dict):
                        attrs["actual_snapshot_rejection_counts"] = {
                            k: v for k, v in counts.items() if isinstance(v, int)
                        }
                attrs[key + "_details_in_store"] = True
        for key in ("dc_hourly_history", "hourly_dc_comparison"):
            value = details.get(key)
            if isinstance(value, dict):
                attrs[key] = {
                    k: (v[:160] if isinstance(v, str) else v)
                    for k, v in value.items()
                    if v is None or isinstance(v, (str, int, float, bool))
                }
                attrs[key + "_full_data_omitted_from_recorder"] = True
        # Compact per-array daily totals for the read-only V3 Lab graph.
        # No hourly arrays or stored snapshot payloads enter Recorder.
        arrays = details.get("array_forecasts")
        if isinstance(arrays, list):
            summary = []
            for item in arrays[:8]:
                if not isinstance(item, dict):
                    continue
                try:
                    forecast = float(item.get("forecast_kwh"))
                    if not (0 <= forecast < 100000):
                        continue
                except (TypeError, ValueError, OverflowError):
                    continue
                summary.append({
                    "name": str(item.get("name") or "PV array")[:60],
                    "forecast_kwh": round(forecast, 3),
                })
            attrs["array_forecasts_summary"] = summary
        for key in ("array_forecasts", "hourly_array_forecasts",
                    "latest_snapshot", "rejections"):
            value = details.get(key)
            if isinstance(value, (list, dict)):
                attrs[key + "_item_count"] = len(value)
                attrs[key + "_full_data_omitted_from_recorder"] = True
        # Hard upper bound for unusual future diagnostics; HA Recorder's
        # state-attribute limit is 16,384 bytes.
        import json
        if len(json.dumps(attrs, ensure_ascii=False, default=str).encode("utf-8")) > 12000:
            attrs = {k: v for k, v in attrs.items()
                     if not isinstance(v, dict)}
            attrs["diagnostics_truncated_for_recorder"] = True
        return attrs

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._store=Store(self.hass,1,"aion_ems_zeus_v3_shadow_snapshots")
        try:
            saved=await self._store.async_load()
            self._snapshots=(saved or {}).get("snapshots",[])[-28:]
        except Exception:
            self._snapshots=[]
        self._dc_store=Store(self.hass,1,"aion_ems_zeus_v3_dc_observations")
        try:
            saved_dc=await self._dc_store.async_load()
            self._dc_samples=(saved_dc or {}).get("samples",[])[-1600:]
        except Exception:
            self._dc_samples=[]
        # Keep a small read-only copy of verified completed-day HA Energy rows.
        # Analytics can be temporarily empty during HA startup/refresh.
        self._actual_store=Store(self.hass,1,"aion_ems_zeus_v3_verified_actual_cache")
        try:
            saved_actual=await self._actual_store.async_load()
            rows=(saved_actual or {}).get("rows",[])
            self._actual_rows_cache=rows[-180:] if isinstance(rows,list) else []
        except Exception:
            self._actual_rows_cache=[]
        self._details["dc_hourly_history"] = self._dc_history_status("waiting_for_first_sample")
        self.async_on_remove(async_track_time_interval(self.hass,self._sample_dc,timedelta(seconds=60)))
        self.async_on_remove(async_track_time_interval(self.hass,self._periodic,timedelta(hours=6)))
        # Weather forecast actions may not be ready during entity setup.
        if self.hass.is_running:
            self.hass.async_create_task(self._sample_dc(None))
            self.hass.async_create_task(self._capture())
        else:
            self.async_on_remove(self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._after_start))

    async def _after_start(self, _event):
        await self._sample_dc(None)
        await self._capture()

    async def _periodic(self, _now):
        await self._capture()

    def _dc_history_status(self, status):
        arrays=(self.core.registry.data.get("home_settings") or {}).get("pv_arrays") or []
        ids=[a.get("power_entity") for a in arrays if isinstance(a,dict)
             and isinstance(a.get("power_entity"),str)]
        now=datetime.now(timezone.utc)
        last=self._dc_samples[-1].get("time_utc") if self._dc_samples else None
        return {"status":status,"mode":"read_only","sample_interval_seconds":60,
                "sample_count":len(self._dc_samples),"last_sample_utc":last,
                "configured_entities":len(ids),
                "hours":hourly_dc_energy(self._dc_samples,ids,now),
                "last_error":self._dc_last_error,
                "note":"Completed UTC hours require >=55 minutes coverage per array."}

    def _hourly_comparison_status(self):
        arrays=(self.core.registry.data.get("home_settings") or {}).get("pv_arrays") or []
        ids=[a.get("power_entity") for a in arrays if isinstance(a,dict) and isinstance(a.get("power_entity"),str)]
        now=datetime.now(timezone.utc)
        return compare_hourly_dc(self._snapshots,
                                 hourly_dc_energy(self._dc_samples,ids,now),ids,now)

    async def _sample_dc(self, _now):
        if self._dc_sample_busy:return
        self._dc_sample_busy=True
        try:
            arrays=(self.core.registry.data.get("home_settings") or {}).get("pv_arrays") or []
            now=datetime.now(timezone.utc)
            sample=collect_sample(arrays,self.hass.states.get,now)
            self._dc_samples=(self._dc_samples+[sample])[-1600:]
            if self._dc_store is not None:
                await self._dc_store.async_save({"samples":self._dc_samples})
            self._dc_last_error=None
            self._details["dc_hourly_history"]=self._dc_history_status("sampling")
            self._details["hourly_dc_comparison"]=self._hourly_comparison_status()
            self.async_write_ha_state()
        except Exception as exc:
            self._dc_last_error={"type":type(exc).__name__,"message":str(exc)[:160]}
            self._details["dc_hourly_history"]=self._dc_history_status("sampling_error")
            self.async_write_ha_state()
        finally:
            self._dc_sample_busy=False

    async def _compare_meteoswiss(self, target, open_meteo):
        entity_id="weather.swiss_meteo_dj31"
        details={"source":entity_id,"status":"unavailable"}
        try:
            if not self.hass.is_running:
                details["status"]="waiting_for_home_assistant"
                return
            if not self.hass.services.has_service("weather","get_forecasts"):
                details["status"]="service_not_registered"
                return
            # HA requires a response-enabled service call for weather.get_forecasts.
            response=await self.hass.services.async_call(
                "weather","get_forecasts",
                service_data={"type":"hourly"},
                target={"entity_id":entity_id},
                blocking=True,return_response=True)
            if not isinstance(response,dict):
                details["status"]="unexpected_response"
                return
            forecast=(response.get(entity_id) or {}).get("forecast")
            if not isinstance(forecast,list):
                details["status"]="no_forecast_rows"
                return
            valid=[]
            for row in forecast:
                if not isinstance(row,dict):
                    continue
                stamp=row.get("datetime")
                radiation=row.get("global_radiation")
                if not isinstance(stamp,str) or not isinstance(radiation,(int,float)):
                    continue
                try:
                    dt=datetime.fromisoformat(stamp)
                except ValueError:
                    continue
                if dt.tzinfo is None:
                    continue
                if dt.astimezone(dt_util.DEFAULT_TIME_ZONE).date().isoformat()==target:
                    valid.append(row)
            details.update(compare_ghi(open_meteo, forecast, target,
                                       str(dt_util.DEFAULT_TIME_ZONE)))
            details["target_local_day_hours"]=len(valid)
            details["note"]="Independent forecast-source GHI comparison, not measured PV accuracy."
        except Exception as exc:
            details.update(status="service_call_failed",
                           error_type=type(exc).__name__,
                           error_message=str(exc)[:240] or "(no message)")
        finally:
            self._details["meteoswiss_comparison"]=details

    def _sunny_geometry_check(self, arrays):
        """Synthetic physically consistent sunlight, not real weather evidence."""
        from .v3_shadow_arrays import hourly_poa, validate_arrays
        from .v3_shadow_geometry import solar_position
        from math import cos, radians
        try:
            geometry=validate_arrays(arrays)
            east=next((i for i,a in enumerate(geometry) if 45<=a["azimuth_deg"]<=135 and a["tilt_deg"]>0),None)
            west=next((i for i,a in enumerate(geometry) if 225<=a["azimuth_deg"]<=315 and a["tilt_deg"]>0),None)
            if east is None or west is None:
                return {"status":"east_west_arrays_not_configured","synthetic_only":True}
            results=[]
            for hour in (9,15):
                end=datetime(2026,3,20,hour,0,tzinfo=timezone.utc)
                # Hourly-mean DNI requires hourly-mean horizontal direct component.
                cosines=[]
                for step in range(60):
                    t=end-timedelta(hours=1)+timedelta(seconds=60*(step+.5))
                    pos=solar_position(t,self.hass.config.latitude,self.hass.config.longitude)
                    cosines.append(max(0.,cos(radians(pos["zenith_deg"]))))
                dni=500.0
                dhi=80.0
                ghi=dhi+dni*sum(cosines)/len(cosines)
                poa=hourly_poa(ghi,dni,dhi,end,self.hass.config.latitude,
                               self.hass.config.longitude,geometry)
                results.append({"hour_end_utc":end.isoformat(),
                                "east_poa_w_m2":round(poa[east],2),
                                "west_poa_w_m2":round(poa[west],2)})
            morning,afternoon=results
            success=(morning["east_poa_w_m2"]>morning["west_poa_w_m2"]+1 and
                     afternoon["west_poa_w_m2"]>afternoon["east_poa_w_m2"]+1)
            return {"status":"pass" if success else "directional_check_failed",
                    "synthetic_only":True,"results":results,
                    "note":"Synthetic geometry check only; not V3 forecast accuracy."}
        except Exception as exc:
            return {"status":"test_unavailable","error_type":type(exc).__name__,
                    "error_message":str(exc)[:160]}

    async def _capture(self):
        if self._running:
            return
        self._running=True
        try:
            now=datetime.now(timezone.utc)
            local_now=dt_util.as_local(now)
            target=(local_now.date()+timedelta(days=1)).isoformat()
            # Fetch both neighboring UTC dates: local midnight is not UTC midnight.
            local_start=datetime.fromisoformat(target).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            utc_start=local_start.astimezone(timezone.utc)
            utc_end=(local_start+timedelta(days=1)).astimezone(timezone.utc)
            first=(utc_start-timedelta(hours=1)).date().isoformat()
            last=utc_end.date().isoformat()
            params={"latitude":self.hass.config.latitude,"longitude":self.hass.config.longitude,
                    "hourly":"shortwave_radiation,direct_normal_irradiance,diffuse_radiation",
                    "timezone":"UTC","start_date":first,"end_date":last}
            session=async_get_clientsession(self.hass)
            async with session.get("https://api.open-meteo.com/v1/forecast",
                                   params=params,timeout=ClientTimeout(total=15)) as resp:
                if resp.status!=200:raise ValueError(f"provider HTTP {resp.status}")
                payload=await resp.json()
            configured=(self.core.registry.data.get("home_settings") or {}).get("pv_arrays")
            result=array_day(payload,target,str(dt_util.DEFAULT_TIME_ZONE),configured)
            self._state=result["status"]
            dc_history=self._details.get("dc_hourly_history") or self._dc_history_status("waiting_for_first_sample")
            self._details={"dc_hourly_history":dc_history,"target_date_utc":target,
                           "forecast_v3_kwh":result.get("experimental_kwh"),
                           "assumed_pr":0.8,
                           "geometry_source":"saved_pv_arrays",
                           "array_forecasts":result["array_forecasts"],
                           "hourly_array_forecasts":result["hourly_forecasts"],
                           "orientation_diagnostics":result["orientation_diagnostics"],
                           "orientation_sunny_test":self._sunny_geometry_check(configured),
                           "dc_observation":dc_power_observation(configured,self.hass.states.get),
                           "dc_observed_at_utc":datetime.now(timezone.utc).isoformat(),
                           "total_capacity_kwp":result["total_capacity_kwp"],
                           "weather_provider":"Open-Meteo",
                           "retrieved_at_utc":datetime.now(timezone.utc).isoformat(),
                           "forecast_issue_time_verified":False,
                           "hourly_rows_accepted":result.get("hours",0),
                           "target_date_local":target,
                           "local_timezone":str(dt_util.DEFAULT_TIME_ZONE),
                           "rejections":result.get("rejections",[])[:5],
                           "note":"Experimental Swiss local-day estimate; provider issue time unverified."}
            # Store captured predictions as evidence, never as training or calibration.
            snapshot={"target_date_local":target,"retrieved_at_utc":self._details["retrieved_at_utc"],
                      "forecast_v3_kwh":result.get("experimental_kwh"),
                      "source":"Open-Meteo","forecast_issue_time_verified":False,
                      "geometry_source":"saved_pv_arrays",
                      "array_forecasts":result["array_forecasts"],
                      "hourly_array_forecasts":result["hourly_forecasts"],
                      "local_timezone":str(dt_util.DEFAULT_TIME_ZONE),
                      "array_power_entities":[a.get("power_entity") for a in (configured or []) if isinstance(a,dict)]}
            if result.get("experimental_kwh") is not None:
                self._snapshots=(self._snapshots+[snapshot])[-28:]
                if self._store is not None:
                    try:
                        await self._store.async_save({"snapshots":self._snapshots})
                        self._details["snapshot_storage"]="saved"
                    except Exception as exc:
                        self._details["snapshot_storage"]=f"error_{type(exc).__name__}"
            self._details["snapshot_count"]=len(self._snapshots)
            self._details["latest_snapshot"]=self._snapshots[-1] if self._snapshots else None
            self._details["hourly_dc_comparison"]=self._hourly_comparison_status()
            # MeteoSwiss is strictly optional: its failure cannot invalidate V3.
            await self._compare_meteoswiss(target,payload)
            # Analytics exposes HA Energy daily statistics. Never use the
            # inverter's 23:00-reset daily meter for completed-day validation.
            try:
                summary=self.core.analytics.summary() or {}
                history=((summary.get("chart_history") or {}).get("total") or [])
                today_local=dt_util.as_local(datetime.now(timezone.utc)).date()
                # Only the exact canonical HA Energy source-set method is
                # admissible; never substitute live watts, inverter daily
                # counters, or estimated solar totals.
                authoritative={}
                for row in history:
                    if not isinstance(row,dict):
                        continue
                    if (row.get("solar_energy_kwh_method") !=
                            "home_assistant_energy_source_set_statistics"):
                        continue
                    if not row.get("solar_energy_kwh_source"):
                        continue
                    try:
                        day=datetime.fromisoformat(row["date"]).date()
                        value=float(row["solar_energy_kwh"])
                        from math import isfinite
                        if day>=today_local or not isfinite(value) or value<0:
                            continue
                    except (KeyError,TypeError,ValueError,OverflowError):
                        continue
                    authoritative[day.isoformat()]={
                        "date":day.isoformat(),"solar_energy_kwh":value,
                        "solar_energy_kwh_method":row["solar_energy_kwh_method"],
                        "solar_energy_kwh_source":row["solar_energy_kwh_source"]}
                # Merge fresh authoritative evidence into a bounded cache.
                # This also avoids replacing yesterday's actuals with an empty
                # transient analytics summary after an HA restart.
                cached={row["date"]:row for row in self._actual_rows_cache
                        if isinstance(row,dict) and row.get("date")}
                cached.update(authoritative)
                self._actual_rows_cache=[cached[k] for k in sorted(cached)[-180:]]
                if authoritative and self._actual_store is not None:
                    await self._actual_store.async_save({"rows":self._actual_rows_cache})
                # Retain all original analytics rows for diagnostic method
                # counts, but prefer cached authoritative evidence when the
                # current summary has not finished loading.
                merged=list(history) if isinstance(history,list) else []
                fresh_dates=set(authoritative)
                merged.extend(row for row in self._actual_rows_cache
                              if row["date"] not in fresh_dates)
                audit=compare_completed_days(self._snapshots,merged,today_local)
                audit["actual_data_source_status"]=(
                    "fresh_ha_energy_statistics" if authoritative else
                    "cached_verified_ha_energy_statistics" if self._actual_rows_cache else
                    "ha_energy_statistics_not_available")
                audit["fresh_authoritative_days"]=len(authoritative)
                audit["cached_authoritative_days"]=len(self._actual_rows_cache)
                if not authoritative:
                    audit["note"]=(str(audit.get("note") or "")+
                        " HA Energy analytics currently has no authoritative rows; "
                        "cached verified completed-day rows used when available.").strip()
                self._details["actual_comparison"]=audit
            except Exception as exc:
                self._details["actual_comparison"]={
                    "status":"actual_source_unavailable","error_type":type(exc).__name__,
                    "comparison_is_accuracy_validated":False}
        except Exception as exc:
            from .v3_shadow_weather_diagnostics import WeatherInputRejected
            # Keep sampling diagnostics and the last successful forecast snapshot
            # visible even when a new weather payload fails validation.
            previous=self._details
            self._state=("weather_input_rejected" if isinstance(exc,WeatherInputRejected)
                         else "weather_unavailable")
            self._details={
                "error_type":type(exc).__name__,
                "error_message":str(exc)[:180],
                "forecast_v3_kwh":None,
                "forecast_issue_time_verified":False,
                "dc_hourly_history":previous.get("dc_hourly_history") or self._dc_history_status("waiting_for_first_sample"),
                "hourly_dc_comparison":self._hourly_comparison_status(),
                "snapshot_count":len(self._snapshots),
                "latest_snapshot":self._snapshots[-1] if self._snapshots else None,
                "weather_failure_classification":("invalid_provider_hourly_values" if isinstance(exc,WeatherInputRejected)
                                                  else "request_or_processing_failure"),
                "weather_rejection":exc.diagnostics() if isinstance(exc,WeatherInputRejected) else None,
                "note":"No weather values substituted; V3 shadow only; V2 unaffected.",
            }
        finally:
            self._running=False
            if self.hass is not None and self.entity_id is not None:
                self.async_write_ha_state()
