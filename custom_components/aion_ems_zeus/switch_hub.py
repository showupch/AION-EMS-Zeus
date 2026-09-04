"""Generic Home Assistant switch control for Zeus Switch Hub."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.util import dt as dt_util


class SwitchHubEngine:
    """Supervised binary switch control using solar surplus or a time window."""

    MIN_ACTION_INTERVAL_SECONDS = 30

    def __init__(self, hass, event_bus, registry, energy_flow) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.registry = registry
        self.energy_flow = energy_flow
        self._last_action_at: dict[str, datetime] = {}
        self._last_known_power_w: dict[str, float] = {}
        self.last: dict[str, Any] = {
            "status": "Ready",
            "device_count": 0,
            "enabled_count": 0,
            "devices": [],
            "safety": "Only explicitly enabled Switch Hub devices may be controlled.",
        }

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            number = float(value)
            return number if number == number else None
        except (TypeError, ValueError):
            return None

    def _configs(self) -> list[dict[str, Any]]:
        rows = self.registry.data.get("switch_hub") or []
        return [dict(row) for row in rows if isinstance(row, dict)]

    def _state_on(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        return bool(state and str(state.state).lower() == "on")

    def _power_w(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        number = self._number(state.state)
        if number is None:
            return None
        unit = str(state.attributes.get("unit_of_measurement") or "W").lower()
        if unit == "kw":
            number *= 1000.0
        return max(0.0, number)

    @staticmethod
    def _time_active(now_local, on_time: str, off_time: str) -> bool:
        try:
            oh, om = [int(x) for x in str(on_time or "00:00").split(":", 1)]
            fh, fm = [int(x) for x in str(off_time or "00:00").split(":", 1)]
            current = now_local.hour * 60 + now_local.minute
            start = oh * 60 + om
            stop = fh * 60 + fm
        except (TypeError, ValueError):
            return False
        if start == stop:
            return False
        if start < stop:
            return start <= current < stop
        return current >= start or current < stop

    def _cooldown_ready(self, key: str, now: datetime) -> bool:
        previous = self._last_action_at.get(key)
        return previous is None or (now - previous).total_seconds() >= self.MIN_ACTION_INTERVAL_SECONDS

    async def async_evaluate(self) -> dict[str, Any]:
        now_utc = datetime.now(timezone.utc)
        now_local = dt_util.now()

        # Actuator decisions must use the same fresh live flow evidence as Zeus.
        try:
            refreshed = self.energy_flow.refresh()
        except Exception:
            refreshed = None
        flow = refreshed if isinstance(refreshed, dict) else (self.energy_flow.summary() or {})
        nested_flows = flow.get("flows") if isinstance(flow.get("flows"), dict) else {}
        export_raw = nested_flows.get("grid_export_power")
        import_raw = nested_flows.get("grid_import_power")
        flow_source = "flows"
        if export_raw is None and import_raw is None:
            export_raw = flow.get("grid_export_power")
            import_raw = flow.get("grid_import_power")
            flow_source = "top_level"

        # v14.8.10.2: Energy Flow power values are canonical {w, kw} payloads.
        def flow_power_w(value):
            if isinstance(value, dict):
                value = value.get("w")
            return self._number(value)

        export_w = max(0.0, flow_power_w(export_raw) or 0.0)
        import_w = max(0.0, flow_power_w(import_raw) or 0.0)

        configs = self._configs()
        prepared: list[dict[str, Any]] = []
        controlled_solar_on_w = 0.0

        # First pass: collect state/power evidence. For SOLAR devices already ON,
        # add their present demand back to grid balance. This reconstructs the
        # surplus that existed before Zeus staged those loads on.
        for index, cfg in enumerate(configs):
            switch_entity = str(cfg.get("switch_entity") or "").strip()
            key = str(cfg.get("id") or switch_entity or f"switch_{index}")
            enabled = cfg.get("control_enabled") is True
            trigger = str(cfg.get("trigger_mode") or "surplus")
            power_entity = str(cfg.get("power_entity") or "").strip() or None
            actual_on = self._state_on(switch_entity) if switch_entity else False
            measured_power = self._power_w(power_entity)
            if measured_power is not None and measured_power > 5:
                self._last_known_power_w[key] = measured_power
            learned_power = self._last_known_power_w.get(key)

            configured_required = self._number(cfg.get("solar_surplus_w"))
            if configured_required is not None and configured_required > 0:
                required_w = configured_required
            elif learned_power is not None and learned_power > 5:
                required_w = learned_power
            else:
                # Migration/default for alpha.4 Switch Hub rows that did not yet
                # have an explicit per-device Solar threshold.
                required_w = 1000.0

            feedback_w = measured_power if measured_power is not None and measured_power > 5 else required_w
            if enabled and trigger == "surplus" and actual_on:
                controlled_solar_on_w += feedback_w

            prepared.append({
                "cfg": cfg,
                "index": index,
                "key": key,
                "switch_entity": switch_entity,
                "enabled": enabled,
                "trigger": trigger,
                "actual_on": actual_on,
                "measured_power": measured_power,
                "learned_power": learned_power,
                "required_w": max(1.0, required_w),
                "feedback_w": max(0.0, feedback_w),
            })

        # Effective pool = what is still being exported, minus current import,
        # plus the demand of Zeus-controlled SOLAR loads already running.
        # This prevents an ON device from hiding the solar resource it consumed.
        effective_surplus_w = max(0.0, export_w - import_w + controlled_solar_on_w)
        remaining_pool_w = effective_surplus_w
        result_rows: list[dict[str, Any]] = []

        # A configured Solar threshold is also the minimum live export reserve
        # while a stage is running. If live export falls below that floor, shed
        # only the highest active Solar stage, then re-evaluate on the next loop.
        # One-at-a-time shedding prevents multiple loads from dropping together
        # on a transient measurement.
        shed_key = None
        for item in reversed(prepared):
            if (
                item["enabled"]
                and item["trigger"] == "surplus"
                and item["actual_on"]
                and export_w + 1.0 < item["required_w"]
            ):
                shed_key = item["key"]
                break

        # Stable registry order is the staging priority. Each eligible SOLAR load
        # reserves its configured requirement from the pool before the next load
        # is considered. This makes 1000 W / 1500 W / 2500 W stages deterministic.
        for item in prepared:
            cfg = item["cfg"]
            key = item["key"]
            switch_entity = item["switch_entity"]
            enabled = item["enabled"]
            trigger = item["trigger"]
            actual_on = item["actual_on"]
            measured_power = item["measured_power"]
            learned_power = item["learned_power"]
            required_w = item["required_w"]

            desired_on = actual_on
            reason = "Zeus Control disabled."
            available_for_device_w = remaining_pool_w
            stage_position = item["index"] + 1

            if enabled and switch_entity:
                if trigger == "time":
                    desired_on = self._time_active(
                        now_local,
                        str(cfg.get("on_time") or "22:00"),
                        str(cfg.get("off_time") or "06:00"),
                    )
                    reason = (
                        f"TIME trigger only · window {cfg.get('on_time') or '22:00'}–{cfg.get('off_time') or '06:00'} "
                        f"is {'active' if desired_on else 'inactive'}. Solar staging is ignored in Time mode."
                    )
                else:
                    available_for_device_w = remaining_pool_w

                    if key == shed_key:
                        desired_on = False
                        reason = (
                            f"SOLAR stage {stage_position} release · live grid export "
                            f"{export_w:.0f} W is below the configured {required_w:.0f} W "
                            f"minimum surplus. Highest active stage is shed first."
                        )
                    elif actual_on:
                        # Lower-priority active stages are held for this cycle if
                        # another higher stage is being shed. The next evaluation
                        # uses the new live export and decides again.
                        desired_on = True
                        remaining_pool_w = max(0.0, remaining_pool_w - required_w)
                        if shed_key:
                            reason = (
                                f"SOLAR stage {stage_position} held while a higher stage is "
                                f"released; live export will be re-evaluated next cycle."
                            )
                        else:
                            reason = (
                                f"SOLAR stage {stage_position} held · live export "
                                f"{export_w:.0f} W is at/above the {required_w:.0f} W minimum."
                            )
                    else:
                        # Starting a new load must leave the same configured
                        # minimum surplus behind after its allocation. This
                        # avoids a 600 W stage repeatedly starting at 600–1199 W
                        # export and then immediately violating its 600 W floor.
                        start_required_w = required_w * 2.0
                        desired_on = (
                            import_w <= 1.0
                            and remaining_pool_w + 1.0 >= start_required_w
                        )
                        if desired_on:
                            remaining_pool_w = max(0.0, remaining_pool_w - required_w)
                            reason = (
                                f"SOLAR stage {stage_position} allocated {required_w:.0f} W · "
                                f"{available_for_device_w:.0f} W available before this stage · "
                                f"{remaining_pool_w:.0f} W remains above the "
                                f"{required_w:.0f} W minimum reserve."
                            )
                        else:
                            reason = (
                                f"SOLAR stage {stage_position} waiting · "
                                f"{available_for_device_w:.0f} W available; "
                                f"{start_required_w:.0f} W is required to start and preserve "
                                f"the {required_w:.0f} W minimum surplus."
                            )

                if desired_on != actual_on and self._cooldown_ready(key, now_utc):
                    service = "turn_on" if desired_on else "turn_off"
                    try:
                        await self.hass.services.async_call(
                            "switch", service, {"entity_id": switch_entity}, blocking=True
                        )
                        self._last_action_at[key] = now_utc
                        actual_on = desired_on
                    except Exception as err:
                        reason = f"Switch command failed: {err}"
                        desired_on = actual_on

            result_rows.append({
                **cfg,
                "actual_state": "on" if actual_on else "off",
                "desired_state": "on" if desired_on else "off",
                "power_w": measured_power,
                "learned_power_w": learned_power,
                "solar_surplus_w": round(required_w, 1),
                "stage_priority": stage_position,
                "grid_export_w": round(export_w, 1),
                "grid_import_w": round(import_w, 1),
                "effective_surplus_w": round(effective_surplus_w, 1),
                "available_for_device_w": round(available_for_device_w, 1),
                "remaining_surplus_w": round(remaining_pool_w, 1),
                "reason": reason,
            })

        self.last = {
            "status": "Ready",
            "device_count": len(result_rows),
            "enabled_count": sum(1 for row in result_rows if row.get("control_enabled") is True),
            "grid_export_w": round(export_w, 1),
            "grid_import_w": round(import_w, 1),
            "controlled_solar_on_w": round(controlled_solar_on_w, 1),
            "effective_surplus_w": round(effective_surplus_w, 1),
            "remaining_surplus_w": round(remaining_pool_w, 1),
            "energy_flow_source": flow_source,
            "devices": result_rows[:30],
            "updated_at": now_utc.isoformat(),
            "anti_chatter_seconds": self.MIN_ACTION_INTERVAL_SECONDS,
            "solar_staging": "Registry order is priority; configured surplus is the live minimum reserve. Highest active stage sheds first below its floor; new stages start only when their allocation can leave that reserve intact.",
            "safety": "Only explicitly enabled Switch Hub devices may be controlled.",
        }
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
