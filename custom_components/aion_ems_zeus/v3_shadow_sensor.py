"""Read-only V3 forecast shadow: isolated from production Forecast V2."""
from __future__ import annotations
from asyncio import timeout
from datetime import datetime, timezone, timedelta
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from aiohttp import ClientTimeout
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from .const import DOMAIN, VERSION
from .v3_shadow_arrays import array_day
from .v3_shadow_dc_observation import dc_power_observation
from .v3_shadow_dc_history import collect_sample, hourly_dc_energy, archive_dc_hours
from .v3_shadow_day_export import MODEL_REVISION, day_bounds, secondary_weather_hours
from .v3_shadow_hourly_comparison import compare_hourly_dc
from .v3_shadow_ghi_comparison import compare_ghi
from .v3_shadow_actual_audit import compare_completed_days, preserve_daily_forecasts, valid_forecast
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
        self._day_export_outlook = {}
        self._running = False
        self._snapshots = []
        self._daily_forecasts = []
        self._daily_store = None
        self._store = None
        self._dc_store = None
        self._dc_samples = []
        self._dc_hour_archive = []
        self._dc_hour_store = None
        self._dc_archived_through = None
        self._dc_sample_busy = False
        self._dc_last_error = None
        self._actual_rows_cache = []
        self._actual_store = None
        self._irr_store = None
        self._irr_samples = []
        self._irr_rejected = 0
        self._irr_status = "waiting"
        self._irr_busy = False
        self._irr_diagnostics = {"status": "waiting_for_first_sample", "accepted_minutes_today": 0, "current_hour_minutes": 0, "rejected_since_start": 0, "completed_hours": 0, "storage": "waiting"}

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
                    attrs["paired_forecast_history"] = value.get("paired_history", [])[-14:]
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
        outlook=details.get("seven_day_outlook")
        if isinstance(outlook,list):
            attrs["seven_day_outlook"]=outlook[:7]
        attrs["daily_archive_preview"] = [{k:r.get(k) for k in ("target_date_local","retrieved_at_utc","forecast_v2_kwh","forecast_v3_kwh")} for r in self._daily_forecasts[-3:]]
        sampling = self._irr_diagnostics
        if isinstance(sampling, dict):
            attrs["local_irradiance_sampling"] = sampling
        chart = details.get("local_weather_chart")
        if isinstance(chart, dict):
            attrs["local_weather_chart"] = {**chart, "observed": self._irr_hourly(datetime.now(timezone.utc))}
            observed = dict(attrs["local_weather_chart"]["observed"])
            for provider in ("open_meteo", "secondary"):
                predicted = dict(chart.get(provider, []))
                common = sorted(observed.keys() & predicted.keys())
                attrs["irradiance_" + provider + "_matched_hours"] = len(common)
                attrs["irradiance_" + provider + "_observed_wh_m2"] = round(sum(observed[k] for k in common), 2) if common else None
                attrs["irradiance_" + provider + "_forecast_wh_m2"] = round(sum(predicted[k] for k in common), 2) if common else None
        # Hard upper bound for unusual future diagnostics; HA Recorder's
        # state-attribute limit is 16,384 bytes.
        import json
        if len(json.dumps(attrs, ensure_ascii=False, default=str).encode("utf-8")) > 12000:
            attrs = {k: v for k, v in attrs.items()
                     if not isinstance(v, dict) or k in ("local_irradiance_sampling", "local_weather_chart")}
            attrs["diagnostics_truncated_for_recorder"] = True
        if len(json.dumps(attrs, ensure_ascii=True, default=str).encode("utf-8")) > 12000:
            attrs.pop("local_weather_chart", None)
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
        self._daily_store=Store(self.hass,1,"aion_ems_zeus_v3_daily_forecasts")
        try:
            daily_saved=await self._daily_store.async_load()
            self._daily_forecasts=preserve_daily_forecasts((daily_saved or {}).get("rows",[]),self._snapshots,str(dt_util.DEFAULT_TIME_ZONE))
        except Exception:
            self._daily_forecasts=preserve_daily_forecasts([],self._snapshots,str(dt_util.DEFAULT_TIME_ZONE))
        self._dc_store=Store(self.hass,1,"aion_ems_zeus_v3_dc_observations")
        try:
            saved_dc=await self._dc_store.async_load()
            self._dc_samples=(saved_dc or {}).get("samples",[])[-1600:]
        except Exception:
            self._dc_samples=[]
        self._dc_hour_store = Store(self.hass, 1, "aion_ems_zeus_v3_dc_hour_archive")
        try:
            saved_hours = (await self._dc_hour_store.async_load() or {}).get("hours", [])
            self._dc_hour_archive = archive_dc_hours(saved_hours, [], datetime.now(timezone.utc))
        except Exception:
            self._dc_hour_archive = []
        self._irr_store = Store(self.hass, 1, "aion_ems_zeus_v3_local_irradiance")
        try:
            self._irr_samples = (await self._irr_store.async_load() or {}).get("samples", [])[-1600:]
        except Exception:
            self._irr_samples = []
        # Keep a small read-only copy of verified completed-day HA Energy rows.
        # Analytics can be temporarily empty during HA startup/refresh.
        self._actual_store=Store(self.hass,1,"aion_ems_zeus_v3_verified_actual_cache")
        try:
            saved_actual=await self._actual_store.async_load()
            rows=(saved_actual or {}).get("rows",[])
            self._actual_rows_cache=rows[-365:] if isinstance(rows,list) else []
        except Exception:
            self._actual_rows_cache=[]
        self._details["dc_hourly_history"] = self._dc_history_status("waiting_for_first_sample")
        self.core.v3_shadow_sensor = self
        self.async_on_remove(self._remove_day_export)
        self.async_on_remove(async_track_time_interval(self.hass,self._sample_dc,timedelta(seconds=60)))
        self.async_on_remove(async_track_time_interval(self.hass,self._irr_tick,timedelta(seconds=60)))
        self.async_on_remove(async_track_time_interval(self.hass,self._periodic,timedelta(hours=6)))
        # Weather forecast actions may not be ready during entity setup.
        if self.hass.is_running:
            self.hass.async_create_task(self._sample_dc(None))
            self.hass.async_create_task(self._irr_tick(None))
            self.hass.async_create_task(self._capture())
        else:
            self.async_on_remove(self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._after_start))

    def _remove_day_export(self):
        if getattr(self.core, "v3_shadow_sensor", None) is self:
            self.core.v3_shadow_sensor = None

    def day_export(self, day):
        from .v3_shadow_day_export import build_day_export
        now = datetime.now(timezone.utc)
        configured = (self.core.registry.data.get("home_settings") or {}).get("pv_arrays") or []
        start, end = day_bounds(day, dt_util.DEFAULT_TIME_ZONE)
        hours = archive_dc_hours(
            self._dc_hour_archive, self._dc_history_status("sampling")["hours"], now,
            window=(start, end),
        )
        return build_day_export(
            day, dt_util.DEFAULT_TIME_ZONE, self._daily_forecasts + self._snapshots,
            self._day_export_outlook.get(day.isoformat()), hours, self._actual_rows_cache, now,
            self._irr_hourly(now, day=day), configured=configured,
        )

    async def _after_start(self, _event):
        await self._irr_tick(None)
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
        return compare_hourly_dc(self._daily_forecasts + self._snapshots,
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
            # Wait for a sample beyond the boundary before freezing the completed hour.
            cutoff = (now - timedelta(minutes=2)).replace(minute=0, second=0, microsecond=0)
            if self._dc_hour_store is not None and cutoff != self._dc_archived_through:
                archived = archive_dc_hours(
                    self._dc_hour_archive, self._details["dc_hourly_history"]["hours"], cutoff,
                )
                if archived != self._dc_hour_archive:
                    await self._dc_hour_store.async_save({"hours": archived})
                    self._dc_hour_archive = archived
                self._dc_archived_through = cutoff
            self._details["hourly_dc_comparison"]=self._hourly_comparison_status()
            self.async_write_ha_state()
        except Exception as exc:
            self._dc_last_error={"type":type(exc).__name__,"message":str(exc)[:160]}
            self._details["dc_hourly_history"]=self._dc_history_status("sampling_error")
            self.async_write_ha_state()
        finally:
            self._dc_sample_busy=False

    def _local_weather_chart(self, payload, secondary=None):
        """Bounded same-local-day forecast means and observed snapshots."""
        from math import isfinite
        now = datetime.now(timezone.utc)
        zone = dt_util.DEFAULT_TIME_ZONE
        day = now.astimezone(zone).date()
        start = datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
        def point(stamp, value, utc_naive=False):
            try:
                t = datetime.fromisoformat(stamp)
                if t.tzinfo is None:
                    if not utc_naive: return None
                    t = t.replace(tzinfo=timezone.utc)
                v = float(value)
                if not isfinite(v) or not 0 <= v <= 1500: return None
                return t.astimezone(timezone.utc), round(v, 1)
            except (TypeError, ValueError, OverflowError):
                return None
        def means(rows, naive=False):
            out = {}
            for stamp, value in rows:
                pair = point(stamp, value, naive)
                if pair and start < pair[0] <= end:
                    out[round((pair[0]-start).total_seconds()/3600, 3)] = pair[1]
            return [[k,v] for k,v in sorted(out.items())][:25]
        h = payload.get("hourly") or {}
        om = means(zip(h.get("time", []), h.get("shortwave_radiation", [])), True) if payload.get("timezone")=="GMT" and payload.get("utc_offset_seconds")==0 else []
        sw = means((r.get("datetime"), r.get("global_radiation")) for r in (secondary or []) if isinstance(r,dict))
        return {"date":day.isoformat(),"hours":(end-start).total_seconds()/3600,
                "open_meteo":om,"secondary":sw,"observed":self._irr_hourly(now)}

    def _irr_entity(self):
        station = (self.core.registry.data.get("sources") or {}).get("local_weather_station") or {}
        return (station.get("entities") or {}).get("solar_radiation") if station.get("enabled") else None

    def _irr_hourly(self, now, *, day=None):
        from math import isfinite
        zone = dt_util.DEFAULT_TIME_ZONE
        local_start = datetime.combine(day or now.astimezone(zone).date(), datetime.min.time(), tzinfo=zone)
        start = local_start.astimezone(timezone.utc)
        next_day = (local_start + timedelta(days=1)).astimezone(timezone.utc)
        entity = self._irr_entity()
        buckets = {}
        for row in self._irr_samples:
            try:
                if not entity or row[0] != entity: continue
                t = datetime.fromisoformat(row[1])
                v = float(row[2])
                if not start <= t <= now or t >= next_day or not isfinite(v) or not 0 <= v <= 1500: continue
                minute = int((t-start).total_seconds()//60)
                buckets.setdefault(minute//60, {})[minute] = v
            except (TypeError, ValueError, IndexError, OverflowError):
                continue
        return [[h+1, round(sum(vals.values())/len(vals),1)] for h,vals in sorted(buckets.items())
                if len(vals)>=55 and start+timedelta(hours=h+1)<=now]

    async def _irr_tick(self, _now):
        if self._irr_busy:
            return
        self._irr_busy = True
        try:
            await self._sample_irradiance(datetime.now(timezone.utc))
        except Exception as exc:
            self._irr_diagnostics = {**self._irr_diagnostics, "status": "sampling_error", "error_type": type(exc).__name__}
        finally:
            self._irr_busy = False
            self.async_write_ha_state()

    async def _sample_irradiance(self, now):
        from math import isfinite
        entity = self._irr_entity()
        state = self.hass.states.get(entity) if entity else None
        accepted = False
        raw = str(state.state)[:80] if state is not None else None
        raw_unit = str(state.attributes.get("unit_of_measurement", ""))[:40] if state is not None else None
        reason = "not_configured" if not entity else "entity_not_found"
        if state is not None:
            unit = raw_unit.replace("²", "2").replace(" ", "").lower()
            if raw.lower() in ("unknown", "unavailable", "none", ""):
                reason = "state_unavailable"
            elif unit not in ("w/m2", "kw/m2"):
                reason = "unsupported_unit"
            else:
                try:
                    v = float(state.state) * (1000 if unit == "kw/m2" else 1)
                except (TypeError, ValueError, OverflowError):
                    reason = "non_numeric_state"
                else:
                    reported = getattr(state, "last_reported", None) or state.last_updated
                    if not isfinite(v) or not 0 <= v <= 1500:
                        reason = "value_out_of_range"
                    elif not timedelta(0) <= now-reported <= timedelta(minutes=10):
                        reason = "stale_report"
                    else:
                        self._irr_samples = (self._irr_samples + [[entity, now.isoformat(), v]])[-1600:]
                        accepted = True
                        reason = "sampling"
        if not accepted:
            self._irr_rejected += 1
        elif self._irr_store is not None:
            try:
                await self._irr_store.async_save({"samples": self._irr_samples})
                self._irr_status = "saved"
            except Exception:
                self._irr_status = "save_failed"
        zone = dt_util.DEFAULT_TIME_ZONE
        start = datetime.combine(now.astimezone(zone).date(), datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
        minutes = set()
        last = None
        last_value = None
        for row in self._irr_samples:
            try:
                t = datetime.fromisoformat(row[1])
                if row[0] == entity and start <= t <= now:
                    minutes.add(int((t-start).total_seconds()//60))
                    last = t.isoformat()
                    last_value = row[2]
            except (TypeError, ValueError, IndexError):
                continue
        hour = int((now-start).total_seconds()//3600)
        previous_rejection = self._irr_diagnostics.get("last_rejection_reason")
        self._irr_diagnostics = {"status": reason, "accepted_minutes_today": len(minutes), "rejected_since_start": self._irr_rejected, "current_hour_minutes": sum(m//60 == hour for m in minutes), "completed_hours": len(self._irr_hourly(now)), "last_success_utc": last, "storage": self._irr_status}
        self._irr_diagnostics.update({"entity_id": str(entity)[:255] if entity else None,
                                      "raw_state": raw, "raw_unit": raw_unit,
                                      "last_success_w_m2": last_value,
                                      "last_rejection_reason": (previous_rejection if accepted else reason)})
        chart = self._details.get("local_weather_chart") or {}
        day = now.astimezone(dt_util.DEFAULT_TIME_ZONE).date()
        if chart.get("date") != day.isoformat():
            chart = self._local_weather_chart({})
        self._details["local_weather_chart"] = {**chart, "observed": self._irr_hourly(now)}

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
            async with timeout(15):
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
            self._details["local_weather_chart"] = self._local_weather_chart(open_meteo, forecast)
            details.update(compare_ghi(open_meteo, forecast, target,
                                       str(dt_util.DEFAULT_TIME_ZONE)))
            details["target_local_day_hours"]=len(valid)
            details["note"]="Independent forecast-source GHI comparison, not measured PV accuracy."
            return forecast
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

    def _capture_v2_day(self, target):
        try:
            summary = self.core.forecast.summary() or {}
            for row in summary.get("daily_forecast") or []:
                if isinstance(row, dict) and row.get("date") == target and row.get("label") == "Tomorrow":
                    value = row.get("expected_solar_kwh")
                    return value if valid_forecast(value) else None
        except Exception:
            # The V2 summary is optional evidence for the independent V3 capture.
            return None
        return None

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
            first=(utc_start-timedelta(days=1,hours=1)).date().isoformat()
            last=(local_start+timedelta(days=6)).astimezone(timezone.utc).date().isoformat()
            params={"latitude":self.hass.config.latitude,"longitude":self.hass.config.longitude,
                    "hourly":"shortwave_radiation,direct_normal_irradiance,diffuse_radiation",
                    "timezone":"UTC","start_date":first,"end_date":last}
            session=async_get_clientsession(self.hass)
            async with session.get("https://api.open-meteo.com/v1/forecast",
                                   params=params,timeout=ClientTimeout(total=15)) as resp:
                if resp.status!=200:raise ValueError(f"provider HTTP {resp.status}")
                payload=await resp.json()
                received = datetime.now(timezone.utc)
                captured_at = received.isoformat()
                v2_value = self._capture_v2_day(target) if received < local_start else None
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
                           "retrieved_at_utc":captured_at,
                           "forecast_issue_time_verified":False,
                           "hourly_rows_accepted":result.get("hours",0),
                           "target_date_local":target,
                           "local_timezone":str(dt_util.DEFAULT_TIME_ZONE),
                           "rejections":result.get("rejections",[])[:5],
                           "note":"Experimental Swiss local-day estimate; provider issue time unverified."}
            # Capture optional secondary evidence alongside this Open-Meteo acquisition.
            self._details["local_weather_chart"] = self._local_weather_chart(payload)
            secondary = await self._compare_meteoswiss(target, payload)
            zone = dt_util.DEFAULT_TIME_ZONE
            capture_metadata = {
                "retrieved_at_utc": captured_at,
                "local_timezone": str(zone),
                "zeus_version": VERSION,
                "model_revision": MODEL_REVISION,
                "array_power_entities": [a.get("power_entity") for a in configured],
                "secondary_retrieved_at_utc": datetime.now(timezone.utc).isoformat() if secondary else None,
            }
            # Seven calendar days, independently validated; never feed outlook into V2.
            outlook=[]
            day_export_outlook = {}
            today=local_now.date()
            today_start=datetime.combine(today, datetime.min.time(), tzinfo=dt_util.DEFAULT_TIME_ZONE)
            frozen=[]
            for saved in self._daily_forecasts:
                try:
                    if saved.get("target_date_local") != today.isoformat():
                        continue
                    captured=datetime.fromisoformat(saved["retrieved_at_utc"])
                    value=float(saved["forecast_v3_kwh"])
                    if captured.tzinfo is not None and captured < today_start and 0 <= value < 100000:
                        frozen.append((captured,value))
                except (TypeError,ValueError,KeyError,OverflowError):
                    continue
            for offset in range(7):
                day=(today+timedelta(days=offset)).isoformat()
                row={"date":day,"forecast_kwh":None,"status":"unavailable"}
                try:
                    daily=result if offset==1 else array_day(payload,day,str(dt_util.DEFAULT_TIME_ZONE),configured)
                    row.update(forecast_kwh=daily["experimental_kwh"],status="experimental")
                    day_export_outlook[day] = {
                        **capture_metadata,
                        "target_date_local": day,
                        "forecast_v3_kwh": daily["experimental_kwh"],
                        "array_forecasts": daily["array_forecasts"],
                        "hourly_array_forecasts": daily["hourly_forecasts"],
                        "secondary_weather_hours": secondary_weather_hours(
                            secondary, *day_bounds(today + timedelta(days=offset), zone)
                        ),
                    }
                    if offset==0:
                        row["pre_day_kwh"]=max(frozen)[1] if frozen else None
                        # Remaining complete forecast hours only; exclude the current partial hour.
                        row["remaining_full_hours_kwh"]=round(sum(h["total_kwh"] for h in daily["hourly_forecasts"]
                            if datetime.fromisoformat(h["hour_end_utc"])-timedelta(hours=1)>=now),3)
                except Exception as exc:
                    row["status"]=type(exc).__name__
                outlook.append(row)
            self._day_export_outlook = day_export_outlook
            self._details["seven_day_outlook"]=outlook
            # Store captured predictions as evidence, never as training or calibration.
            snapshot = {
                **day_export_outlook[target],
                "source": "Open-Meteo",
                "forecast_issue_time_verified": False,
                "geometry_source": "saved_pv_arrays",
            }
            if v2_value is not None:
                snapshot["forecast_v2_kwh"] = v2_value
            if result.get("experimental_kwh") is not None:
                self._daily_forecasts=preserve_daily_forecasts(self._daily_forecasts,self._snapshots+[snapshot],str(dt_util.DEFAULT_TIME_ZONE))
                try:
                    await self._daily_store.async_save({"rows":self._daily_forecasts})
                    self._details["daily_archive_storage"]="saved"
                except Exception as exc:
                    self._details["daily_archive_storage"]=f"error_{type(exc).__name__}"
                self._snapshots=(self._snapshots+[snapshot])[-28:]
                if self._store is not None:
                    try:
                        await self._store.async_save({"snapshots":self._snapshots})
                        self._details["snapshot_storage"]="saved"
                    except Exception as exc:
                        self._details["snapshot_storage"]=f"error_{type(exc).__name__}"
            self._details["daily_archive_count"] = len({row["target_date_local"] for row in self._daily_forecasts})
            self._details["snapshot_count"]=len(self._snapshots)
            self._details["latest_snapshot"]=self._snapshots[-1] if self._snapshots else None
            self._details["hourly_dc_comparison"]=self._hourly_comparison_status()
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
                self._actual_rows_cache=[cached[k] for k in sorted(cached)[-365:]]
                if authoritative and self._actual_store is not None:
                    await self._actual_store.async_save({"rows":self._actual_rows_cache})
                # Retain all original analytics rows for diagnostic method
                # counts, but prefer cached authoritative evidence when the
                # current summary has not finished loading.
                merged=list(history) if isinstance(history,list) else []
                fresh_dates=set(authoritative)
                merged.extend(row for row in self._actual_rows_cache
                              if row["date"] not in fresh_dates)
                audit=compare_completed_days(self._daily_forecasts,merged,today_local)
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
                "local_weather_chart":previous.get("local_weather_chart"),
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
