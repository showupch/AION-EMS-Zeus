"""Observation and Knowledge Engine for AION EMS Zeus v11.4.

Transforms live state transitions and compact Data Lake history into recorder-safe,
persistent knowledge objects. The engine is read-only and recommendation-only.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean, median
from typing import Any

from homeassistant.helpers.storage import Store

STORAGE_KEY = "aion_ems_zeus.observation_knowledge"
STORAGE_VERSION = 1


class ObservationKnowledgeEngine:
    """Build persistent observations, patterns, habits and evidence chains."""

    MAX_OBSERVATIONS = 240
    MAX_EVOLUTION = 120

    def __init__(self, hass, event_bus, energy_flow, data_lake, hyper, weather, registry) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.energy_flow = energy_flow
        self.data_lake = data_lake
        self.hyper = hyper
        self.weather = weather
        self.registry = registry
        self.store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = {
            "schema_version": 1,
            "previous": {},
            "observations": [],
            "evolution": [],
            "metadata": {"created_at": datetime.now(timezone.utc).isoformat()},
        }
        self._save_pending = False
        self.last: dict[str, Any] = {
            "status": "Learning",
            "headline": "Observation & Knowledge Engine is learning.",
            "observations": [], "patterns": [], "habits": [], "weather_intelligence": [],
            "evolution": [], "knowledge_graph": [], "evidence": [],
            "confidence": 0, "recorder_safe": True,
            "safety": "Recommendation only. No autonomous control.",
        }

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            self.data.update(stored)
        self.data.setdefault("previous", {})
        self.data.setdefault("observations", [])
        self.data.setdefault("evolution", [])
        self.data.setdefault("metadata", {})

    async def async_save(self) -> None:
        self._save_pending = False
        await self.store.async_save(self.data)

    def _schedule_save(self) -> None:
        if not self._save_pending:
            self._save_pending = True
            self.hass.async_create_task(self.async_save())

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        try:
            return float(value if value not in (None, "", "unknown", "unavailable") else default)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _iso_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _flow_values(self) -> dict[str, float]:
        flow = self.energy_flow.summary() or {}
        return {
            "solar": self._num(flow.get("solar_power") or flow.get("solar_power_w")),
            "home": self._num(flow.get("house_power") or flow.get("house_power_w")),
            "import": self._num(flow.get("grid_import_power") or flow.get("grid_import_power_w")),
            "export": self._num(flow.get("grid_export_power") or flow.get("grid_export_power_w")),
            "charge": self._num(flow.get("battery_charge_power") or flow.get("battery_charge_power_w")),
            "discharge": self._num(flow.get("battery_discharge_power") or flow.get("battery_discharge_power_w")),
            "soc": self._num(flow.get("battery_soc_percent"), -1),
        }

    def _registered_device_activity(self) -> dict[str, dict[str, Any]]:
        """Return compact active/inactive evidence for registered consuming devices."""
        result: dict[str, dict[str, Any]] = {}
        devices = list((getattr(self.registry, "data", {}) or {}).get("devices", []) or [])
        for device in devices:
            if not isinstance(device, dict) or device.get("enabled") is False:
                continue
            device_id = str(device.get("id") or "").strip()
            if not device_id:
                continue
            dtype = str(device.get("type") or "device").strip().lower()
            # Inverters are valid Timeline devices: users need to see each
            # physical inverter start/stop independently. Only non-load source
            # objects that have no meaningful run session stay excluded here.
            if dtype in {"battery", "energy_meter", "smart_meter"}:
                continue
            name = str(device.get("name") or device_id).strip()
            identity = " ".join(
                str(device.get(key) or "")
                for key in ("type", "category", "role", "name", "manufacturer", "model")
            ).lower()
            inverter_like = bool(
                dtype in {"solar_inverter", "pv_inverter", "inverter", "hybrid_inverter", "battery_inverter"}
                or device.get("hybrid_inverter") is True
                or "fronius" in identity
                or "inverter" in identity
            )
            power_entity = str(device.get("power_entity") or "").strip()
            power_w = 0.0
            power_valid = False

            def _read_power(entity_id: str) -> tuple[float, bool]:
                if not entity_id:
                    return 0.0, False
                state = self.hass.states.get(entity_id)
                if state is None:
                    return 0.0, False
                raw_state = str(state.state or "").strip().lower()
                if raw_state in {"", "unknown", "unavailable", "none", "nan"}:
                    return 0.0, False
                try:
                    raw = float(state.state)
                    unit = str(state.attributes.get("unit_of_measurement") or "W").strip().lower()
                    return (raw * 1000.0 if unit == "kw" else raw), True
                except (TypeError, ValueError):
                    return 0.0, False

            power_w, power_valid = _read_power(power_entity)
            # v14.8.10.16: inverter Timeline activity must evaluate every mapped
            # live production source, not only fall back to solar_power_entity
            # when power_entity is unavailable. Hybrid integrations can keep a
            # valid-but-zero/lagging generic power entity while the dedicated
            # PV/AC source is already producing. Treating that zero as authority
            # caused false "stopped" events while the inverter was visibly on.
            #
            # Inverter output sensors also differ in sign convention, so Timeline
            # activity uses magnitude. This is activity detection only; canonical
            # energy-flow direction/sign calculations are intentionally untouched.
            if inverter_like:
                inverter_evidence: list[float] = []
                if power_valid:
                    inverter_evidence.append(abs(power_w))
                solar_power_entity = str(device.get("solar_power_entity") or "").strip()
                if solar_power_entity and solar_power_entity != power_entity:
                    solar_power, solar_power_valid = _read_power(solar_power_entity)
                    if solar_power_valid:
                        inverter_evidence.append(abs(solar_power))
                if inverter_evidence:
                    power_w = max(inverter_evidence)
                    power_valid = True
                else:
                    power_w, power_valid = 0.0, False

            # v14.8.10.15: Heat Pump running state follows the whole-unit live
            # electrical power mapping. More than 25 W means the Heat Pump has
            # started; 25 W or less means stopped. Classified Heating/DHW power
            # sensors are deliberately NOT used to decide the overall running
            # state because those sensors may publish later in the cycle. They
            # remain independent mode/attribution measurements for the UI.

            # v14.8.2-alpha.17: registered-load activity uses hysteresis and
            # preserves the previous state across unavailable samples. This avoids
            # Timeline chatter when a device hovers around the old 50 W threshold
            # or briefly reports unknown/unavailable during polling.
            previous_item = (self.data.get("previous_devices") or {}).get(device_id) or {}
            previously_active = bool(previous_item.get("active"))
            if not power_valid:
                active = previously_active
            elif dtype == "heat_pump":
                # User-confirmed Heat Pump semantics: >25 W = started. Heating
                # and DHW classified power can arrive later and must not delay
                # the device Running state or Timeline START event.
                active = power_w > 25.0
            elif previously_active:
                active = power_w >= 25.0
            else:
                active = power_w > 75.0
            activity = ""
            if dtype == "heat_pump":
                compressor_entity = str(device.get("compressor_state_entity") or device.get("compressor_activity_entity") or "").strip()
                compressor_state = self.hass.states.get(compressor_entity) if compressor_entity else None
                activity = str(getattr(compressor_state, "state", "") or "").strip().lower()
                if activity:
                    compressor_active = activity not in {"off", "idle", "standby", "inactive", "0", "false", "unknown", "unavailable"}
                    # Positive compressor evidence can confirm activity, but an
                    # idle/stale state must not override clear measured electrical
                    # consumption from the Heat Pump circuit.
                    active = bool(active or compressor_active)
            result[device_id] = {"name": name, "type": dtype, "active": bool(active), "power_w": max(power_w, 0.0), "activity": activity, "inverter_like": inverter_like}
        return result

    def _observe_registered_devices(self) -> None:
        """Persist confirmed registered-device start/stop transitions.

        v14.8.4-rc.7 makes Timeline activity session-aware.  Short appliance
        dips and EV charger 0 W negotiation windows no longer terminate a
        session immediately.  Confirmation is time-based so the result does not
        depend on how often unrelated source entities happen to refresh Zeus.

        Confirm windows:
        * EV charger: start 5 s, stop 180 s
        * Generic registered load: start 10 s, stop 60 s
        * Water heater: start 5 s, stop 30 s
        * Heat pump: start immediate, stop immediate after the >25 W running threshold
        * Registered inverter: start/stop immediate after 75/25 W hysteresis

        A candidate that returns to the stable state before its window expires
        is cancelled without producing Timeline noise.
        """
        current = self._registered_device_activity()
        previous = dict(self.data.get("previous_devices") or {})
        pending = self.data.setdefault("device_transition_pending", {})
        stable_next: dict[str, dict[str, Any]] = dict(previous)
        now_dt = datetime.now(timezone.utc)

        def _window_seconds(dtype: str, candidate_active: bool, inverter_like: bool = False) -> int:
            dtype = str(dtype or "device")
            if inverter_like:
                # v14.8.10.13: inverter power entities may emit only the edge
                # update (0 -> production or production -> 0). A timed pending
                # window can therefore never receive a second callback. Power
                # already has 75/25 W hysteresis, so confirm the edge now.
                return 0
            if dtype == "ev_charger":
                return 5 if candidate_active else 180
            if dtype == "heat_pump":
                # v14.8.10.9: Heat Pump electrical activity uses the confirmed >25 W running threshold
                # hysteresis, and split power/compressor evidence is watched live.
                # Do not leave either edge in a time-based pending state: after the
                # source transition there may be no second HA event to re-enter this
                # method, which previously left STOP pending until the 15-minute
                # safety refresh. Confirm both START and STOP on the live refresh.
                return 0
            if dtype == "water_heater":
                return 5 if candidate_active else 30
            return 10 if candidate_active else 60

        def _parse_iso(value: Any) -> datetime | None:
            try:
                parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        if previous:
            for device_id, item in current.items():
                before = previous.get(device_id)
                if not isinstance(before, dict):
                    stable_next[device_id] = item
                    pending.pop(device_id, None)
                    continue

                was_active, candidate_active = bool(before.get("active")), bool(item.get("active"))
                if was_active == candidate_active:
                    pending.pop(device_id, None)
                    stable_next[device_id] = item
                    continue

                dtype = str(item.get("type") or "device")
                target = "on" if candidate_active else "off"
                entry = pending.get(device_id) if isinstance(pending.get(device_id), dict) else {}
                first_seen = _parse_iso(entry.get("first_seen")) if entry.get("target") == target else None
                if first_seen is None:
                    first_seen = now_dt
                    pending[device_id] = {
                        "target": target,
                        "first_seen": first_seen.isoformat(timespec="seconds"),
                        "last_power_w": round(float(item.get("power_w") or 0.0), 1),
                    }

                required_seconds = _window_seconds(dtype, candidate_active, bool(item.get("inverter_like")))
                elapsed_seconds = max(0.0, (now_dt - first_seen).total_seconds())
                if elapsed_seconds < required_seconds:
                    held = dict(item)
                    held["active"] = was_active
                    stable_next[device_id] = held
                    continue

                pending.pop(device_id, None)
                stable_next[device_id] = item
                name = str(item.get("name") or device_id)
                power_w = float(item.get("power_w") or 0.0)
                if dtype == "heat_pump":
                    activity = str(item.get("activity") or "").strip()
                    if candidate_active:
                        mode = activity.replace("_", " ").title() if activity and activity not in {"on", "true", "1"} else "Running"
                        title = f"{name} {mode.lower()} started" if mode != "Running" else f"{name} started"
                    else:
                        title = f"{name} stopped"
                    kind = "heat_pump"
                elif dtype == "water_heater":
                    title, kind = f"{name} {'started' if candidate_active else 'stopped'}", "water_heater"
                elif dtype == "ev_charger":
                    title, kind = f"{name} charging {'started' if candidate_active else 'stopped'}", "ev"
                else:
                    title, kind = f"{name} {'started' if candidate_active else 'stopped'}", "device"
                detail = (
                    f"Measured power {power_w:.0f} W after {required_seconds} s confirmation."
                    if candidate_active
                    else f"Measured power remained below the inactive threshold for {required_seconds} s before confirming stop."
                )
                self._add_observation(
                    kind, title, detail,
                    {
                        "device_id": device_id,
                        "power_w": round(power_w, 1),
                        "confirmed_seconds": required_seconds,
                        "session_aware": dtype == "ev_charger",
                    },
                    100,
                )
        elif current:
            for device_id, item in current.items():
                stable_next[device_id] = item
                if not bool(item.get("active")):
                    continue
                name = str(item.get("name") or device_id)
                dtype = str(item.get("type") or "device")
                power_w = float(item.get("power_w") or 0.0)
                if dtype == "ev_charger":
                    title, kind = f"{name} charging active", "ev"
                elif dtype == "heat_pump":
                    activity = str(item.get("activity") or "").strip()
                    mode = activity.replace("_", " ").title() if activity and activity not in {"on", "true", "1"} else "Running"
                    title, kind = f"{name} {mode.lower()} active", "heat_pump"
                elif dtype == "water_heater":
                    title, kind = f"{name} active", "water_heater"
                else:
                    title, kind = f"{name} active", "device"
                self._add_observation(kind, title, f"Active at Timeline baseline · measured power {power_w:.0f} W.", {"device_id": device_id, "power_w": round(power_w, 1), "baseline": True}, 100)

        live_ids = set(current)
        stable_next = {k: v for k, v in stable_next.items() if k in live_ids}
        for device_id in list(pending):
            if device_id not in live_ids:
                pending.pop(device_id, None)
        self.data["previous_devices"] = stable_next

    def _add_observation(self, kind: str, title: str, detail: str, evidence: dict[str, Any], confidence: int = 100) -> None:
        now = self._iso_now()
        # Deduplicate only identical events within the same minute. Hour-level
        # fingerprints suppressed valid start -> stop -> start cycles in one hour.
        fingerprint = f"{kind}:{title}:{now[:16]}"
        if any(x.get("fingerprint") == fingerprint for x in self.data["observations"][:20]):
            return
        self.data["observations"].insert(0, {
            "id": f"obs-{int(datetime.now(timezone.utc).timestamp())}-{len(self.data['observations'])}",
            "fingerprint": fingerprint,
            "type": kind,
            "time": now,
            "title": title,
            "detail": detail,
            "confidence": confidence,
            "evidence": evidence,
        })
        self.data["observations"] = self.data["observations"][:self.MAX_OBSERVATIONS]
        self._schedule_save()

    def _observe_transitions(self, current: dict[str, float]) -> None:
        prev = self.data.get("previous") or {}
        threshold = 50.0
        if prev:
            transitions = [
                (prev.get("solar", 0) <= threshold < current["solar"], "solar", "Solar production started", f"Solar output rose to {current['solar']:.0f} W."),
                (prev.get("solar", 0) > threshold >= current["solar"], "solar", "Solar production stopped", "Measured solar output fell below the active-flow threshold."),
                (prev.get("import", 0) <= threshold < current["import"], "grid", "Grid import started", f"The grid began supplying {current['import']:.0f} W."),
                (prev.get("export", 0) <= threshold < current["export"], "grid", "Grid export started", f"Surplus export reached {current['export']:.0f} W."),
                (prev.get("charge", 0) <= threshold < current["charge"], "battery", "Battery charging started", f"Battery charge power reached {current['charge']:.0f} W."),
                (prev.get("discharge", 0) <= threshold < current["discharge"], "battery", "Battery support started", f"Battery discharge power reached {current['discharge']:.0f} W."),
                (prev.get("soc", -1) < 99 <= current["soc"], "battery", "Battery reached full", "Battery state of charge reached the full threshold."),
                (prev.get("soc", 101) > 20 >= current["soc"] >= 0, "battery", "Battery reached reserve", f"Battery state of charge reached {current['soc']:.0f}%."),
                (current["home"] > max(prev.get("home", 0) * 1.8, 1500) and current["home"] - prev.get("home", 0) > 800, "home", "Large load increase detected", f"Home demand increased to {current['home']:.0f} W."),
            ]
            for condition, kind, title, detail in transitions:
                if condition:
                    self._add_observation(kind, title, detail, current.copy(), 100)
        elif not self.data["observations"]:
            self._add_observation("system", "Knowledge observation started", "Zeus began converting live transitions into persistent knowledge objects.", current.copy(), 100)
        self.data["previous"] = current

    def _daily_rows(self) -> list[dict[str, Any]]:
        daily = self.data_lake.data.get("daily_summaries", {}) if hasattr(self.data_lake, "data") else {}
        return [daily[k] for k in sorted(daily)][-400:]

    def _snapshots(self) -> list[dict[str, Any]]:
        return list((self.data_lake.data.get("snapshots", []) if hasattr(self.data_lake, "data") else [])[-20160:])

    def _patterns(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(rows) < 4:
            return []
        weekdays: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            try:
                day = datetime.fromisoformat(str(row.get("date"))).strftime("%A")
            except (TypeError, ValueError):
                continue
            weekdays[day].append(row)
        patterns = []
        for metric, label, unit in (
            ("solar_energy_kwh", "solar production", "kWh"),
            ("house_energy_kwh", "home demand", "kWh"),
            ("grid_import_energy_kwh", "grid import", "kWh"),
        ):
            averages = {day: mean(self._num(x.get(metric)) for x in vals) for day, vals in weekdays.items() if vals}
            if averages:
                day = max(averages, key=averages.get)
                count = len(weekdays[day])
                patterns.append({
                    "type": "weekday", "trigger": day, "behavior": f"Highest average {label}",
                    "value": round(averages[day], 2), "unit": unit, "observations": count,
                    "confidence": min(98, 45 + count * 8),
                    "explanation": f"Across {count} measured {day}s, average {label} was {averages[day]:.2f} {unit}.",
                })
        return patterns[:6]

    def _habits(self, snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(snapshots) < 60:
            return []
        hourly: dict[int, list[float]] = defaultdict(list)
        export_hours: list[int] = []
        full_hours: list[int] = []
        for snap in snapshots:
            try:
                dt = datetime.fromisoformat(str(snap.get("timestamp", "")).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            f = snap.get("flows", {}) or {}
            home = self._num(f.get("house_power_w"), -1)
            if 0 <= home < 30000:
                hourly[dt.hour].append(home)
            if self._num(f.get("grid_export_power_w")) > 100:
                export_hours.append(dt.hour)
            if self._num(f.get("battery_soc_percent"), -1) >= 99:
                full_hours.append(dt.hour)
        habits = []
        if hourly:
            morning = {h: mean(v) for h, v in hourly.items() if 5 <= h <= 11 and v}
            evening = {h: mean(v) for h, v in hourly.items() if 16 <= h <= 23 and v}
            for name, values in (("Morning demand peak", morning), ("Evening demand peak", evening)):
                if values:
                    hour = max(values, key=values.get)
                    habits.append({"name": name, "time": f"{hour:02d}:00", "value_w": round(values[hour]), "observations": len(hourly[hour]), "confidence": min(98, 40 + len(hourly[hour]) // 8)})
        if export_hours:
            habits.append({"name": "Typical export window", "time": f"{int(median(export_hours)):02d}:00", "observations": len(export_hours), "confidence": min(98, 45 + len(export_hours) // 20)})
        if full_hours:
            habits.append({"name": "Typical battery-full window", "time": f"{int(median(full_hours)):02d}:00", "observations": len(full_hours), "confidence": min(98, 45 + len(full_hours) // 20)})
        return habits[:8]

    def _weather_intelligence(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        weather = self.weather.summary() or {}
        current = weather.get("condition") or weather.get("weather_condition") or weather.get("summary")
        hyper = self.hyper.summary() or {}
        discoveries = hyper.get("discoveries") or []
        results = []
        if current:
            results.append({"condition": str(current), "relationship": "Current weather context is included in solar and demand reasoning.", "confidence": weather.get("confidence", 50), "evidence_count": len(rows)})
        for item in discoveries:
            text = f"{item.get('title','')} {item.get('detail','')}".lower()
            if any(word in text for word in ("weather", "cloud", "temperature", "sunny", "rain")):
                results.append({"condition": item.get("title"), "relationship": item.get("detail"), "confidence": hyper.get("confidence", 0), "evidence_count": len(rows)})
        return results[:5]

    def _evolution(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(rows) < 14:
            return self.data.get("evolution", [])[:8]
        recent, previous = rows[-7:], rows[-14:-7]
        checks = []
        for key, label, lower_better in (
            ("solar_energy_kwh", "Solar production", False),
            ("house_energy_kwh", "Home demand", True),
            ("grid_import_energy_kwh", "Grid dependence", True),
            ("grid_export_energy_kwh", "Solar export", False),
        ):
            a = mean(self._num(x.get(key)) for x in recent)
            b = mean(self._num(x.get(key)) for x in previous)
            if b <= 0:
                continue
            pct = (a - b) / b * 100
            if abs(pct) >= 5:
                improved = pct <= 0 if lower_better else pct >= 0
                checks.append({"metric": label, "change_percent": round(pct, 1), "direction": "improved" if improved else "changed", "period": "last 7 days vs previous 7 days", "confidence": min(95, 55 + len(rows))})
        existing = self.data.get("evolution", [])
        for item in checks:
            signature = f"{item['metric']}:{item['direction']}:{datetime.now(timezone.utc).date().isoformat()}"
            if not any(x.get("signature") == signature for x in existing):
                existing.insert(0, {**item, "signature": signature, "detected_at": self._iso_now()})
        self.data["evolution"] = existing[:self.MAX_EVOLUTION]
        if checks:
            self._schedule_save()
        return self.data["evolution"][:8]

    @staticmethod
    def _knowledge_graph(patterns: list[dict[str, Any]], habits: list[dict[str, Any]], hyper: dict[str, Any]) -> list[dict[str, Any]]:
        graph = [
            {"from": "Weather", "to": "Solar", "relation": "influences"},
            {"from": "Solar", "to": "Battery", "relation": "charges"},
            {"from": "Solar", "to": "Home", "relation": "supplies"},
            {"from": "Battery", "to": "Grid import", "relation": "reduces"},
            {"from": "Grid export", "to": "Finance", "relation": "creates revenue"},
            {"from": "Self-consumption", "to": "Solar savings", "relation": "creates value"},
        ]
        if patterns:
            graph.append({"from": patterns[0].get("trigger", "Pattern"), "to": patterns[0].get("behavior", "Behavior"), "relation": "predicts"})
        if habits:
            graph.append({"from": "House routine", "to": habits[0].get("name", "Habit"), "relation": "contains"})
        if hyper.get("opportunities"):
            graph.append({"from": "Discovered pattern", "to": "Opportunity", "relation": "supports"})
        return graph[:10]

    def refresh(self) -> dict[str, Any]:
        current = self._flow_values()
        self._observe_transitions(current)
        self._observe_registered_devices()
        rows = self._daily_rows()
        snapshots = self._snapshots()
        hyper = self.hyper.summary() or {}
        patterns = self._patterns(rows)
        habits = self._habits(snapshots)
        weather_intelligence = self._weather_intelligence(rows)
        evolution = self._evolution(rows)
        graph = self._knowledge_graph(patterns, habits, hyper)
        observations = self.data.get("observations", [])[:20]
        evidence = []
        for pattern in patterns[:4]:
            evidence.append({"claim": pattern.get("behavior"), "because": pattern.get("explanation"), "confidence": pattern.get("confidence"), "observations": pattern.get("observations")})
        for item in (hyper.get("discoveries") or [])[:3]:
            evidence.append({"claim": item.get("title"), "because": item.get("detail"), "confidence": hyper.get("confidence", 0), "observations": len(rows)})
        confidence = min(99, int(25 + min(len(rows), 40) * 1.5 + min(len(snapshots), 1000) / 50))
        knowledge_objects = len(observations) + len(patterns) + len(habits) + len(weather_intelligence) + len(evolution)
        headline = observations[0]["title"] if observations else "Zeus is building structured knowledge about your home."
        self.last = {
            "status": "Ready" if rows or snapshots else "Learning",
            "headline": headline,
            "generated_at": self._iso_now(),
            "observation_count": len(self.data.get("observations", [])),
            "knowledge_object_count": knowledge_objects,
            "observations": observations,
            "patterns": patterns,
            "habits": habits,
            "weather_intelligence": weather_intelligence,
            "evolution": evolution,
            "knowledge_graph": graph,
            "evidence": evidence[:8],
            "confidence": confidence,
            "confidence_label": "High" if confidence >= 80 else "Medium" if confidence >= 55 else "Low",
            "storage": "persistent_local_store",
            "incremental": True,
            "recorder_safe": True,
            "safety": "Recommendation only. Observation & Knowledge Engine never controls devices.",
        }
        self.event_bus.publish("ObservationKnowledgeUpdated", "ObservationKnowledgeEngine", {"status": self.last["status"], "knowledge_objects": knowledge_objects, "confidence": confidence})
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
