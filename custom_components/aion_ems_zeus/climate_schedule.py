"""Opt-in, persistent room-temperature schedules. No energy control coupling."""
from __future__ import annotations

import logging
from datetime import timedelta
from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

class ClimateScheduleManager:
    def __init__(self, hass):
        self.hass = hass
        self.store = Store(hass, 1, "aion_ems_zeus_climate_schedules")
        self.schedules = {}
        self._last_applied = {}
        self._unsubscribe = None

    async def async_start(self):
        data = await self.store.async_load() or {}
        self.schedules = data.get("schedules", {}) if isinstance(data, dict) else {}
        self._unsubscribe = async_track_time_interval(self.hass, self._tick, timedelta(seconds=30))
        await self._tick(None)

    async def async_stop(self):
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    async def async_save(self, entity_id, enabled, days, slots):
        if not isinstance(entity_id, str) or not entity_id.startswith("climate.") or entity_id not in self.hass.states.async_entity_ids("climate"):
            raise ValueError("A valid existing climate entity is required")
        if not isinstance(days, list) or not days or any(type(x) is not int or x not in range(7) for x in days):
            raise ValueError("Choose valid weekdays")
        if not isinstance(slots, list) or len(slots) > 24:
            raise ValueError("Invalid number of periods")
        state = self.hass.states.get(entity_id)
        minimum = float(state.attributes.get("min_temp", 5))
        maximum = float(state.attributes.get("max_temp", 35))
        cleaned = []
        def minute(value):
            if not (isinstance(value, str) and len(value) == 5 and value[2] == ":"
                    and value[:2].isdigit() and value[3:].isdigit()
                    and 0 <= int(value[:2]) < 24 and 0 <= int(value[3:]) < 60):
                raise ValueError("Invalid time")
            return int(value[:2]) * 60 + int(value[3:])

        for slot in slots:
            if not isinstance(slot, dict):
                raise ValueError("Invalid period")
            start, end = slot.get("from"), slot.get("to")
            a, b = minute(start), minute(end)
            if a == b:
                raise ValueError("A period must have a nonzero duration")
            try:
                target = float(slot.get("temperature"))
            except (TypeError, ValueError, OverflowError):
                raise ValueError("Invalid target temperature") from None
            import math
            if not math.isfinite(target) or not minimum <= target <= maximum:
                raise ValueError("Target outside thermostat limits")
            cleaned.append({"from": start, "to": end, "temperature": target})
        cleaned.sort(key=lambda x: x["from"])
        # Build a circular week of intervals. Midnight ends and overnight
        # carry-over are handled for every selected start day, including Sun→Mon.
        intervals = []
        for day in set(days):
            for slot in cleaned:
                a, b = minute(slot["from"]), minute(slot["to"])
                if b <= a:
                    b += 1440
                start = day * 1440 + a
                end = day * 1440 + b
                for shift in (-10080, 0, 10080):
                    intervals.append((start + shift, end + shift))
        intervals.sort()
        if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
            raise ValueError("Overlapping periods are not allowed")
        if enabled and not cleaned:
            raise ValueError("An enabled schedule needs at least one period")
        self.schedules[entity_id] = {"enabled":bool(enabled),"days":sorted(set(days)),"slots":cleaned}
        self._last_applied.pop(entity_id, None)
        await self.store.async_save({"schedules":self.schedules})
        return self.schedules[entity_id]

    async def _tick(self, _now):
        local = dt_util.as_local(dt_util.utcnow())
        weekday = local.weekday()
        current_time = local.strftime("%H:%M")
        yesterday = (weekday - 1) % 7
        for entity_id, schedule in list(self.schedules.items()):
            if not schedule.get("enabled"):
                continue
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unknown", "unavailable"):
                continue
            days = schedule.get("days", [])
            slots = schedule.get("slots", [])
            # A slot crossing midnight belongs to its start weekday. At
            # 00:00–end, honor yesterday's enabled schedule as well.
            slot = next((s for s in slots if weekday in days and
                         (s["from"] <= current_time < s["to"] if s["from"] < s["to"]
                          else current_time >= s["from"])), None)
            carried = False
            if slot is None:
                slot = next((s for s in slots if yesterday in days and
                             s["from"] > s["to"] and current_time < s["to"]), None)
                carried = slot is not None
            if slot is None:
                continue  # Gaps and period ends never change the thermostat.
            key = ((local.date() - timedelta(days=1) if carried else local.date()).isoformat(), slot["from"], slot["to"], slot["temperature"])
            if self._last_applied.get(entity_id) == key:
                continue
            # If a human has changed the thermostat during the active period,
            # do not repeatedly override that manual choice.
            try:
                target = float(state.attributes.get("temperature"))
                if abs(target - slot["temperature"]) < 0.1:
                    self._last_applied[entity_id] = key
                    continue
                await self.hass.services.async_call("climate","set_temperature",{"entity_id":entity_id,"temperature":slot["temperature"]},blocking=True)
                self._last_applied[entity_id] = key
            except Exception:
                _LOGGER.exception("Unable to apply Zeus climate schedule for %s", entity_id)
