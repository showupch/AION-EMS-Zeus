"""AION EMS Zeus Smart Control & Safety.

The my-PV ELWA path supports deliberately supervised Modbus execution while
remaining fail-closed by default. The go-e Charger MQTT profile can publish the
validated IDS PV-surplus evidence payload only after explicit per-device control
permission is granted; otherwise it remains completely passive.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from typing import Any

from .device_profiles import effective_elwa_control, get_device_profile
from .elwa_direct_modbus import ElwaDirectModbusClient, ElwaDirectTarget, ElwaModbusError


class SmartControlSafetyEngine:
    """Evaluate control permissions, safety gates and dry-run decisions."""

    VERSION = 1

    def __init__(self, event_bus, registry) -> None:
        self.event_bus = event_bus
        self.registry = registry
        self.data: dict[str, Any] = {}
        self._simulation_history: list[dict[str, Any]] = []
        self._last_simulation_signature: dict[str, tuple] = {}
        # Runtime-only hysteresis state for the simulated ELWA grid-backup strategy.
        # It is deliberately fail-closed on restart: backup can only arm again once
        # the boiler/DHW temperature is observed below its configured start point.
        self._grid_backup_active: dict[str, bool] = {}
        # Runtime-only SOLAR latch used only to distinguish OFF/start semantics
        # from an already-running ELWA. While SOLAR is active, the grid meter
        # reports only the *remaining* export after ELWA consumption. Zeus must
        # reconstruct the pre-ELWA surplus from measured export + measured ELWA
        # power, otherwise its own load can drive export below the start threshold
        # and create start/stop oscillation. The latch is fail-closed on restart.
        self._solar_active: dict[str, bool] = {}
        # Real execution runtime exists only for the explicitly supervised my-PV ELWA path.
        # Persistent configuration defaults to master-disabled; runtime state is never restored as active.
        self._execution_runtime: dict[str, dict[str, Any]] = {}
        self._elwa_direct_clients: dict[str, ElwaDirectModbusClient] = {}
        self.refresh()

    @staticmethod
    def _device_capability(device: dict[str, Any], global_mode: str) -> dict[str, Any]:
        enabled = bool(device.get("enabled", True))
        controllable = bool(device.get("controllable", False))
        permission = bool(device.get("control_permission", False))

        # Future actuator metadata is intentionally descriptive only in V1.
        actuator_type = str(device.get("actuator_type") or "").strip() or None
        actuator_entity = str(
            device.get("control_entity")
            or device.get("automation_entity")
            or ""
        ).strip() or None
        actuator_service = str(device.get("control_service") or "").strip() or None
        control_hub = str(device.get("control_hub") or "").strip() or None
        control_unit = device.get("control_unit")
        control_address = device.get("control_address")
        direct_elwa_ip = str(device.get("control_elwa_ip") or "").strip() or None
        control_mqtt_topic = str(device.get("control_mqtt_topic") or "").strip() or None
        # Treat zero as a valid Modbus unit/address. Readiness depends on
        # presence, not truthiness. A configured ELWA IP selects Zeus Direct
        # Modbus and supplies the standard port/unit/register defaults itself.
        direct_modbus_complete = actuator_type == "modbus" and direct_elwa_ip is not None
        modbus_complete = (
            actuator_type == "modbus"
            and (
                direct_modbus_complete
                or (
                    control_hub is not None
                    and control_unit not in (None, "")
                    and control_address not in (None, "")
                )
            )
        )
        mqtt_complete = (
            actuator_type == "mqtt"
            and str(device.get("control_service") or "").strip() == "mqtt.publish"
            and control_mqtt_topic is not None
        )
        has_actuator = bool(
            actuator_entity
            or actuator_service
            or modbus_complete
            or mqtt_complete
        )

        reasons: list[str] = []
        if not enabled:
            reasons.append("Device is disabled.")
        if not controllable:
            reasons.append("Device is not marked controllable.")
        if not permission:
            reasons.append("Explicit Zeus control permission is not granted.")
        if not has_actuator:
            reasons.append("No control actuator/entity/service is configured.")
        if global_mode == "recommendation_only":
            reasons.append("Global safety mode is Recommendation Only.")

        # Foundation V1 has no execution path by design.  Even a completely
        # configured device therefore remains blocked from automatic actuation.
        execution_allowed = False

        if not enabled:
            status = "disabled"
        elif not controllable:
            status = "observe_only"
        elif not permission:
            status = "permission_required"
        elif not has_actuator:
            status = "actuator_required"
        elif global_mode == "recommendation_only":
            status = "recommendation_only"
        else:
            status = "foundation_ready"

        return {
            "device_id": device.get("id"),
            "name": device.get("name") or device.get("id"),
            "type": device.get("type") or "custom",
            "device_profile": device.get("device_profile"),
            "profile": get_device_profile(device.get("device_profile")),
            "enabled": enabled,
            "controllable": controllable,
            "control_permission": permission,
            "actuator_configured": has_actuator,
            "actuator_type": actuator_type,
            "actuator_entity": actuator_entity,
            "actuator_service": actuator_service,
            "power_limits_w": {
                "min": device.get("control_min_power_w"),
                "max": device.get("control_max_power_w"),
            },
            "modbus": {
                "mode": "zeus_direct" if direct_elwa_ip else "home_assistant",
                "host": direct_elwa_ip,
                "hub": control_hub,
                "unit": 1 if direct_elwa_ip else control_unit,
                "address": 1000 if direct_elwa_ip else control_address,
                "temperature_address": 1001 if direct_elwa_ip else None,
                "complete": modbus_complete,
            } if actuator_type == "modbus" else None,
            "mqtt": {
                "topic": control_mqtt_topic,
                "complete": mqtt_complete,
            } if actuator_type == "mqtt" else None,
            "safety_evidence": {
                "boiler_temperature_entity": device.get("control_boiler_temperature_entity"),
                "element_temperature_entity": device.get("control_element_temperature_entity"),
                "surplus_entity": device.get("control_surplus_entity"),
                "lockout_entity": device.get("control_lockout_entity"),
                "stop_temperature_c": device.get("control_stop_temperature_c"),
                "restart_temperature_c": device.get("control_restart_temperature_c"),
            },
            "status": status,
            "execution_allowed": execution_allowed,
            "blocked_reasons": reasons or [
                "Smart Control Foundation V1 has no actuator execution path."
            ],
        }


    def _state_number(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.registry.hass.states.get(entity_id) if hasattr(self.registry, "hass") else None
        if state is None:
            return None
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        return value

    def _state_text(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        state = self.registry.hass.states.get(entity_id) if hasattr(self.registry, "hass") else None
        if state is None:
            return None
        return str(state.state)

    def _direct_elwa_ip(self, device: dict[str, Any]) -> str | None:
        value = str(device.get("control_elwa_ip") or "").strip()
        return value or None

    def _direct_elwa_client(self, device: dict[str, Any]) -> ElwaDirectModbusClient | None:
        host = self._direct_elwa_ip(device)
        if not host:
            return None
        device_id = str(device.get("id") or "unknown")
        cached = self._elwa_direct_clients.get(device_id)
        if cached is not None and cached.target.host == host:
            return cached
        target = ElwaDirectTarget.from_host(host)
        client = ElwaDirectModbusClient(target)
        self._elwa_direct_clients[device_id] = client
        return client

    async def _async_poll_direct_elwa(self, device: dict[str, Any]) -> bool:
        """Poll standard ELWA registers when Zeus Direct Modbus is configured."""
        host = self._direct_elwa_ip(device)
        if not host:
            return True
        device_id = str(device.get("id") or "unknown")
        runtime = self._execution_runtime.setdefault(device_id, {})
        try:
            client = self._direct_elwa_client(device)
            if client is None:
                raise ElwaModbusError("ELWA direct client is unavailable")
            snapshot = await client.read_snapshot()
        except Exception as err:
            runtime.update(
                direct_connected=False,
                direct_status="UNAVAILABLE",
                direct_last_error=str(err),
            )
            return False
        now = datetime.now(timezone.utc)
        runtime.update(
            direct_connected=True,
            direct_status="CONNECTED",
            direct_host=host,
            direct_power_w=float(snapshot["power_w"]),
            direct_temperature_c=float(snapshot["temperature_c"]),
            direct_last_read_at=now.isoformat(),
            direct_last_error=None,
        )
        return True

    async def _async_poll_all_direct_elwa(self, registry_devices: dict[str, dict[str, Any]]) -> None:
        for device in registry_devices.values():
            if str(device.get("device_profile") or "") != "my_pv_elwa":
                continue
            if not self._direct_elwa_ip(device):
                continue
            await self._async_poll_direct_elwa(device)

    def _simulate_water_heater(self, device: dict[str, Any], capability: dict[str, Any]) -> dict[str, Any]:
        """Return a recommendation-only target for the ELWA Water Heater profile.

        Strategy order is safety gates -> SOLAR -> GRID BACKUP. GRID BACKUP is
        eligible only when SOLAR cannot produce a useful request. The returned
        decision is also the source for the supervised ELWA execution loop.
        """
        runtime = self._execution_runtime.get(str(device.get("id") or "unknown")) or {}
        current_power_w = self._state_number(device.get("power_entity"))
        if current_power_w is None and self._direct_elwa_ip(device):
            direct_power = runtime.get("direct_power_w")
            current_power_w = float(direct_power) if direct_power is not None else None
        surplus_w = self._state_number(device.get("control_surplus_entity"))
        boiler_c = self._state_number(device.get("control_boiler_temperature_entity"))
        element_c = self._state_number(device.get("control_element_temperature_entity"))
        if element_c is None and self._direct_elwa_ip(device):
            direct_temp = runtime.get("direct_temperature_c")
            element_c = float(direct_temp) if direct_temp is not None else None
        lockout_raw = self._state_text(device.get("control_lockout_entity"))
        lockout_on = str(lockout_raw or "").lower() in {"on", "true", "1", "yes"}

        policy = effective_elwa_control(device)
        min_w = policy["minimum_power_w"]
        grid_backup_power_w = max(min_w, policy["maximum_power_w"])
        solar_max_w = max(min_w, policy["solar_maximum_power_w"])
        # max_w remains the execution/device ceiling exposed in diagnostics.
        max_w = solar_max_w
        stop_c = policy["boiler_stop_temperature_c"]
        restart_c = policy["boiler_restart_temperature_c"]
        solar_start_threshold_w = policy["solar_start_threshold_w"]
        solar_factor = policy["solar_factor"]
        solar_export_reserve_w = policy["solar_export_reserve_w"]
        taper_start_c = policy["element_taper_start_c"]
        element_hard_stop_c = policy["element_hard_stop_c"]
        grid_start_c = policy["grid_backup_start_c"]
        grid_stop_c = policy["grid_backup_stop_c"]

        requested_w = 0.0
        decision = "BLOCKED"
        reason = "Insufficient live evidence."
        allowed = False
        strategy = "none"
        temperature_factor = 1.0
        device_id = str(device.get("id") or "unknown")
        solar_was_active = bool(self._solar_active.get(device_id, False))

        # While an already-running SOLAR request is physically drawing power,
        # the export sensor only shows the leftover headroom. Reconstruct the
        # available pre-ELWA surplus from *measured* ELWA power. This deliberately
        # does not use commanded power and is never applied to GRID BACKUP, so a
        # grid-fed 2 kW backup request cannot masquerade as solar surplus.
        measured_elwa_for_solar_w = 0.0
        if solar_was_active and current_power_w is not None and current_power_w > 0:
            measured_elwa_for_solar_w = max(0.0, current_power_w)
        effective_solar_surplus_w = (surplus_w + measured_elwa_for_solar_w) if surplus_w is not None else None

        # Maintain separate GRID BACKUP hysteresis.  Solar always has priority,
        # but an armed backup remains armed through the 50-55 °C band so it can
        # resume if usable solar disappears before DHW reaches 55 °C.
        grid_backup_active = bool(self._grid_backup_active.get(device_id, False))
        if boiler_c is not None and grid_start_c < grid_stop_c:
            if boiler_c < grid_start_c:
                grid_backup_active = True
            elif boiler_c >= grid_stop_c:
                grid_backup_active = False
        self._grid_backup_active[device_id] = grid_backup_active

        # Element-temperature taper helper.  Element temperature alone drives
        # taper; boiler/DHW temperature never changes this factor.
        if element_c is not None:
            if element_c >= element_hard_stop_c:
                temperature_factor = 0.0
            elif element_c >= taper_start_c:
                span = max(0.1, element_hard_stop_c - taper_start_c)
                temperature_factor = max(
                    0.25,
                    1.0 - ((element_c - taper_start_c) / span) * 0.75,
                )

        # Shared hard safety gates. Existing proven boiler lockout semantics are
        # unchanged and apply before either SOLAR or GRID BACKUP is considered.
        if capability.get("status") not in {"recommendation_only", "foundation_ready"}:
            reason = f"Control candidate gate is {capability.get('status') or 'not ready'}."
        elif surplus_w is None:
            reason = "Solar surplus entity has no numeric live value."
        elif boiler_c is None:
            reason = "Boiler temperature entity has no numeric live value."
        elif grid_start_c >= grid_stop_c:
            reason = (
                f"Grid backup thresholds are invalid: start {grid_start_c:.1f} °C "
                f"must be below stop {grid_stop_c:.1f} °C."
            )
        elif lockout_on:
            reason = "Temperature lockout is active."
        elif stop_c is not None and boiler_c >= stop_c:
            reason = f"Boiler temperature {boiler_c:.1f} °C reached stop threshold {stop_c:.1f} °C."
        elif restart_c is not None and boiler_c > restart_c and (current_power_w or 0) <= 0:
            reason = f"Boiler is above restart threshold {restart_c:.1f} °C; restart is held."
        elif element_c is not None and element_c >= element_hard_stop_c:
            reason = (
                f"Element temperature {element_c:.1f} °C reached the "
                f"{element_hard_stop_c:.1f} °C hard stop."
            )
        else:
            # 1) Proven SOLAR strategy.  Calculate its real useful request first;
            # GRID BACKUP may only run when this result is not usable.
            solar_requested_w = 0.0
            solar_reason = ""

            # OFF/START and RUNNING deliberately use different evidence semantics:
            # - OFF: require the configured raw export start threshold (800 W).
            # - RUNNING: do not compare leftover export directly with 800 W. Instead
            #   reconstruct available surplus as export + measured ELWA power, then
            #   keep running only while that basis still produces a useful request.
            # This prevents ELWA from stopping merely because its own consumption
            # reduced grid export below the start threshold.
            solar_basis_w = surplus_w
            solar_start_ok = surplus_w >= solar_start_threshold_w
            solar_feedback_active = bool(solar_was_active and measured_elwa_for_solar_w > 0)
            if solar_feedback_active:
                solar_basis_w = effective_solar_surplus_w
                solar_start_ok = True

            if solar_start_ok and solar_basis_w is not None:
                # Closed-loop solar modulation. OFF->ON still uses the 800 W raw
                # export threshold. Once ELWA is running, regulate directly on
                # the grid meter so Zeus trims ELWA to preserve a small positive
                # export reserve instead of causing import or stop/start hunting.
                #
                # RUNNING: preserve the configured export reserve first, then
                # apply the configured solar factor only to the remaining export
                # headroom and add that increment to the already-running ELWA.
                # Example: ELWA 2030 W + 0.90 * (1730 W export - 400 W reserve)
                #          = 3227 W before the device maximum clamp.
                # STARTING: target = raw_export * solar_factor (proven HA rule).
                if solar_feedback_active:
                    usable_export_headroom_w = max(0.0, surplus_w - solar_export_reserve_w)
                    dynamic_solar_target_w = measured_elwa_for_solar_w + (usable_export_headroom_w * solar_factor)
                else:
                    dynamic_solar_target_w = solar_basis_w * solar_factor
                dynamic_solar_target_w = min(max_w, max(0.0, dynamic_solar_target_w))
                dynamic_solar_target_w = round(dynamic_solar_target_w / 10.0) * 10.0
                solar_power_w = dynamic_solar_target_w
                solar_requested_w = max(0.0, solar_power_w * temperature_factor)
                if solar_feedback_active:
                    solar_reason = (
                        f"SOLAR running feedback: {surplus_w:.0f} W remaining export + "
                        f"{measured_elwa_for_solar_w:.0f} W measured ELWA = "
                        f"{solar_basis_w:.0f} W effective surplus; preserve "
                        f"{solar_export_reserve_w:.0f} W export reserve and apply "
                        f"{solar_factor * 100:.0f}% to remaining export headroom, "
                        f"target {dynamic_solar_target_w:.0f} W."
                    )
                else:
                    solar_reason = (
                        f"SOLAR start: {surplus_w:.0f} W export is at/above "
                        f"{solar_start_threshold_w:.0f} W; request "
                        f"{solar_factor * 100:.0f}% up to {max_w:.0f} W."
                    )
                if element_c is not None and element_c >= taper_start_c:
                    solar_reason += (
                        f" Element {element_c:.1f} °C applies "
                        f"{temperature_factor * 100:.0f}% power taper."
                    )
                if 0.0 < solar_requested_w < min_w:
                    solar_reason += (
                        f" Result {solar_requested_w:.0f} W is below configured "
                        f"minimum {min_w:.0f} W; SOLAR stops."
                    )
                    solar_requested_w = 0.0

            if solar_requested_w > 0:
                requested_w = solar_requested_w
                strategy = "solar"
                allowed = True
                decision = "ALLOWED"
                reason = solar_reason
                self._solar_active[device_id] = True
            elif grid_backup_active:
                self._solar_active[device_id] = False
                # 2) Separate comfort/safety GRID BACKUP strategy. It is only
                # reached when there is no usable SOLAR request. It requests the
                # configured maximum (2000 W for the ELWA profile), then applies
                # the same element-temperature safety taper and minimum floor.
                if element_c is None:
                    reason = "Grid backup is armed, but element temperature evidence is unavailable; fail-closed."
                else:
                    grid_requested_w = grid_backup_power_w * temperature_factor
                    if 0.0 < grid_requested_w < min_w:
                        reason = (
                            f"GRID BACKUP is armed, but element taper leaves {grid_requested_w:.0f} W, "
                            f"below configured minimum {min_w:.0f} W; request held at 0 W."
                        )
                        grid_requested_w = 0.0
                    if grid_requested_w > 0:
                        requested_w = grid_requested_w
                        strategy = "grid_backup"
                        allowed = True
                        decision = "ALLOWED"
                        solar_context = (
                            f"export {surplus_w:.0f} W is below {solar_start_threshold_w:.0f} W SOLAR start"
                            if surplus_w < solar_start_threshold_w
                            else "SOLAR request is not usable after safety taper/minimum"
                        )
                        if boiler_c < grid_start_c:
                            grid_state_text = (
                                f"DHW {boiler_c:.1f} °C is below the {grid_start_c:.1f} °C "
                                "GRID BACKUP start threshold"
                            )
                        else:
                            grid_state_text = (
                                f"DHW {boiler_c:.1f} °C is inside the armed "
                                f"{grid_start_c:.1f}-{grid_stop_c:.1f} °C hysteresis band"
                            )
                        reason = (
                            f"GRID BACKUP active: {grid_state_text} and {solar_context}. "
                            f"Request {grid_requested_w:.0f} W (configured GRID BACKUP power {grid_backup_power_w:.0f} W)."
                        )
                        if element_c >= taper_start_c:
                            reason += (
                                f" Element {element_c:.1f} °C applies "
                                f"{temperature_factor * 100:.0f}% safety taper."
                            )
            else:
                self._solar_active[device_id] = False
                if surplus_w < solar_start_threshold_w:
                    reason = (
                        f"Export surplus {surplus_w:.0f} W is below the proven "
                        f"{solar_start_threshold_w:.0f} W SOLAR start threshold; "
                        f"GRID BACKUP is inactive until DHW falls below {grid_start_c:.1f} °C."
                    )
                else:
                    reason = solar_reason or "No usable SOLAR request and GRID BACKUP is inactive."

        requested_w = round(max(0.0, requested_w))
        actual_power_w = None if current_power_w is None else round(max(0.0, current_power_w), 1)
        power_error_w = None if actual_power_w is None else round(actual_power_w - requested_w, 1)
        power_error_percent = None
        if actual_power_w is not None and requested_w > 0:
            power_error_percent = round((power_error_w / requested_w) * 100.0, 1)
        comparison = "waiting"
        if actual_power_w is not None:
            if requested_w <= 0:
                comparison = "actual_idle" if actual_power_w < 100 else "actual_running_while_simulated_off"
            else:
                tolerance_w = max(100.0, requested_w * 0.10)
                comparison = "match" if abs(power_error_w) <= tolerance_w else "outside_tolerance"

        # Command Preview is derived from the completed simulation decision.
        # It must never participate in candidate discovery/readiness, otherwise a
        # preview bug could hide an otherwise valid simulator candidate.
        actuator_type = str(device.get("actuator_type") or "").strip()
        direct_elwa_ip = self._direct_elwa_ip(device)
        command_hub = str(device.get("control_hub") or "").strip() or None
        command_unit = device.get("control_unit")
        command_address = device.get("control_address")
        direct_target_ready = actuator_type == "modbus" and direct_elwa_ip is not None
        command_target_ready = (
            actuator_type == "modbus"
            and (
                direct_target_ready
                or (
                    command_hub is not None
                    and command_unit not in (None, "")
                    and command_address not in (None, "")
                )
            )
        )
        if direct_target_ready:
            command_unit = 1
            command_address = 1000
        if not command_target_ready:
            command_action = "NOT READY"
        elif requested_w <= 0:
            command_action = "STOP (0)"
        elif comparison == "match":
            command_action = "REFRESH"
        else:
            command_action = "WRITE"

        command_preview = {
            "target_ready": command_target_ready,
            "transport": ("zeus.direct_modbus" if direct_target_ready else "modbus.write_register") if actuator_type == "modbus" else actuator_type or "not_configured",
            "host": direct_elwa_ip,
            "hub": command_hub,
            "unit": command_unit,
            "address": command_address,
            "value": int(requested_w),
            "action": command_action,
            "keepalive_interval_s": policy["keepalive_interval_s"],
            "recompute_before_each_write": True,
            "execution_enabled": bool(device.get("control_execution_master_enabled", False)),
            "would_execute": False,
        }

        # Execution-readiness gates are deliberately separate from the current
        # simulator decision.  They answer one question only: is the ELWA device
        # configuration/evidence complete enough for a future supervised handover?
        # The final master execution lock remains hard OFF in this build.
        profile_id = str(device.get("device_profile") or "")
        element_entity_configured = bool(str(device.get("control_element_temperature_entity") or "").strip())
        lockout_entity_id = str(device.get("control_lockout_entity") or "").strip() or None
        lockout_entity_obj = (
            self.registry.hass.states.get(lockout_entity_id)
            if lockout_entity_id and hasattr(self.registry, "hass")
            else None
        )
        lockout_entity_present = lockout_entity_obj is not None
        lockout_entity_configured = lockout_entity_id is not None
        # Optional external lockout portability: a genuinely absent lockout
        # helper cannot be active and must not interlock an otherwise safe ELWA.
        # If the configured entity exists but reports unknown/unavailable, fail
        # closed because Zeus cannot prove the external lockout is inactive.
        lockout_state_known = bool(
            not lockout_entity_configured
            or not lockout_entity_present
            or str(lockout_raw or "").lower() in {
                "on", "off", "true", "false", "1", "0", "yes", "no"
            }
        )
        if not lockout_entity_configured:
            lockout_detail = "Optional · not configured · Zeus internal temperature limits active"
        elif not lockout_entity_present:
            lockout_detail = f"{lockout_entity_id}: not found · no external lockout is active"
        else:
            lockout_detail = f"{lockout_entity_id}: {lockout_raw or 'unknown'}"
        min_limit_valid = min_w > 0
        max_limit_valid = max_w >= min_w and max_w <= 3500
        boiler_hysteresis_valid = restart_c < stop_c
        taper_valid = taper_start_c < element_hard_stop_c
        grid_hysteresis_valid = grid_start_c < grid_stop_c
        keepalive_valid = 5 <= int(policy["keepalive_interval_s"]) <= 3600

        # Control ownership / supervised handover interlock. Existing ELWA
        # devices default to Home Assistant ownership and therefore remain
        # intentionally blocked from future Zeus execution readiness.
        control_owner = str(device.get("control_owner") or "home_assistant").strip().lower()
        previous_controller_entity = str(device.get("control_previous_controller_entity") or "").strip() or None
        previous_controller_obj = (
            self.registry.hass.states.get(previous_controller_entity)
            if previous_controller_entity and hasattr(self.registry, "hass")
            else None
        )
        previous_controller_present = previous_controller_obj is not None
        previous_controller_state = str(previous_controller_obj.state) if previous_controller_obj is not None else None
        # Portability rule: a genuinely absent legacy controller cannot compete
        # for the ELWA register.  Treat a configured-but-no-longer-existing entity
        # the same as no previous controller.  A controller entity that *exists*
        # but reports unknown/unavailable still fails closed because Zeus cannot
        # prove that writer is inactive.
        previous_controller_off = bool(
            previous_controller_entity is None
            or not previous_controller_present
            or str(previous_controller_state or "").lower() == "off"
        )
        if previous_controller_entity is None:
            previous_controller_detail = "Optional · no previous HA controller configured"
        elif not previous_controller_present:
            previous_controller_detail = f"{previous_controller_entity}: not found · no active HA writer detected"
        else:
            previous_controller_detail = f"{previous_controller_entity}: {previous_controller_state or 'unknown'}"
        handover_confirmed = bool(device.get("control_handover_confirmed", False))
        zeus_owner_selected = control_owner == "zeus"

        readiness_gates = [
            {"id": "device_enabled", "label": "Device enabled", "passed": bool(device.get("enabled", True)), "detail": "Registry device must be enabled."},
            {"id": "controllable", "label": "Marked controllable", "passed": bool(device.get("controllable", False)), "detail": "Device must be explicitly marked controllable."},
            {"id": "permission", "label": "Zeus permission granted", "passed": bool(device.get("control_permission", False)), "detail": "Per-device control permission is mandatory."},
            {"id": "profile", "label": "my-PV ELWA profile selected", "passed": profile_id == "my_pv_elwa", "detail": f"Current profile: {profile_id or 'legacy / none'}."},
            {"id": "actuator", "label": "Modbus actuator selected", "passed": actuator_type == "modbus", "detail": f"Actuator: {actuator_type or 'not configured'}."},
            {"id": "target", "label": "ELWA Modbus target ready", "passed": bool(command_target_ready and (not direct_target_ready or runtime.get("direct_connected"))), "detail": ((f"Zeus Direct · {direct_elwa_ip}:502 · unit 1 · power 1000 · temp 1001 · {runtime.get('direct_status') or 'WAITING'}." + (f" {runtime.get('direct_last_error')}" if runtime.get('direct_last_error') else "")) if direct_target_ready else f"{command_hub or '—'} · unit {command_unit if command_unit not in (None, '') else '—'} · address {command_address if command_address not in (None, '') else '—'}." )},
            {"id": "power_limits", "label": "Power limits valid", "passed": min_limit_valid and max_limit_valid, "detail": f"Minimum {min_w:.0f} W · SOLAR max {solar_max_w:.0f} W · GRID BACKUP {grid_backup_power_w:.0f} W."},
            {"id": "surplus", "label": "Solar surplus evidence live", "passed": surplus_w is not None, "detail": str(device.get("control_surplus_entity") or "No entity configured")},
            {"id": "boiler", "label": "Boiler/DHW evidence live", "passed": boiler_c is not None, "detail": str(device.get("control_boiler_temperature_entity") or "No entity configured")},
            {"id": "element", "label": "Element temperature evidence", "passed": (not element_entity_configured) or element_c is not None, "detail": (str(device.get("control_element_temperature_entity")) if element_entity_configured else "Optional · not configured")},
            {"id": "lockout", "label": "External lockout evidence", "passed": lockout_state_known, "detail": lockout_detail},
            {"id": "boiler_hysteresis", "label": "Boiler hysteresis valid", "passed": boiler_hysteresis_valid, "detail": f"Restart {restart_c:.1f} °C < stop {stop_c:.1f} °C."},
            {"id": "element_taper", "label": "Element taper range valid", "passed": taper_valid, "detail": f"Taper {taper_start_c:.1f} °C < hard stop {element_hard_stop_c:.1f} °C."},
            {"id": "grid_hysteresis", "label": "Grid Backup hysteresis valid", "passed": grid_hysteresis_valid, "detail": f"Start {grid_start_c:.1f} °C < stop {grid_stop_c:.1f} °C."},
            {"id": "keepalive", "label": "Keepalive interval valid", "passed": keepalive_valid, "detail": f"{int(policy['keepalive_interval_s'])} s; safety range 5–3600 s."},
            {"id": "ownership", "label": "Zeus control ownership selected", "passed": zeus_owner_selected, "detail": "Current owner: Zeus." if zeus_owner_selected else "Current owner: Home Assistant. Zeus remains interlocked."},
            {"id": "previous_controller", "label": "Existing HA controller not active", "passed": previous_controller_off, "detail": previous_controller_detail},
            {"id": "handover_confirmation", "label": "Supervised handover confirmed", "passed": zeus_owner_selected and handover_confirmed, "detail": "Operator confirmed exclusive Zeus ownership." if handover_confirmed else "Explicit handover confirmation is still required."},
        ]
        passed_gates = sum(1 for gate in readiness_gates if gate["passed"])
        readiness_complete = passed_gates == len(readiness_gates)
        master_enabled = bool(device.get("control_execution_master_enabled", False))
        emergency_stop = bool(device.get("control_emergency_stop", False))
        strict_target_ok = bool(
            (direct_target_ready and command_unit == 1 and command_address == 1000)
            or (
                str(command_hub or "").strip().lower() == "elwa"
                and command_unit not in (None, "")
                and command_address not in (None, "")
                and int(command_unit) == 1
                and int(command_address) == 1000
            )
        )
        execution_readiness = {
            "ready_except_master_lock": readiness_complete,
            "status": "READY" if readiness_complete else "NOT READY",
            "passed_gates": passed_gates,
            "total_gates": len(readiness_gates),
            "failed_gates": len(readiness_gates) - passed_gates,
            "gates": readiness_gates,
            "master_execution_lock": not master_enabled,
            "master_execution_lock_reason": (
                "Real ELWA execution is explicitly enabled for this device; all readiness and arm gates remain mandatory."
                if master_enabled else
                "Real ELWA execution is disabled by default after upgrade. Enable it only during an explicit supervised handover."
            ),
            "strict_execution_target_ok": strict_target_ok,
            "emergency_stop": emergency_stop,
            "would_execute": False,
        }

        # Supervised execution arm. The persisted request and explicit operator
        # confirmation are still separate from the master execution enable.
        # Effective arming is automatically revoked whenever any readiness gate fails.
        arm_requested = bool(device.get("control_execution_arm_requested", False))
        arm_confirmed = bool(device.get("control_execution_arm_confirmed", False))
        arm_eligible = readiness_complete
        arm_effective = bool(arm_eligible and arm_requested and arm_confirmed)
        auto_disarmed = bool((arm_requested or arm_confirmed) and not arm_eligible)
        if not arm_eligible:
            arm_state = "DISARMED"
            arm_label = "Not eligible to arm"
            arm_next = "Complete all 18 execution-readiness gates before requesting a supervised arm."
        elif not arm_requested:
            arm_state = "ELIGIBLE"
            arm_label = "Eligible for supervised arm request"
            arm_next = "Request execution arm only after the supervised handover is complete."
        elif not arm_confirmed:
            arm_state = "AWAIT_CONFIRMATION"
            arm_label = "Arm request recorded; confirmation required"
            arm_next = "Confirm the execution-arm intent. Real writes still require the separate master enable."
        else:
            arm_state = "ARMED"
            arm_label = "Supervised arm state active"
            arm_next = "All gates remain under continuous evaluation. Any gate failure automatically disarms execution."
        execution_arm = {
            "state": arm_state,
            "label": arm_label,
            "next_action": arm_next,
            "eligible": arm_eligible,
            "requested": arm_requested,
            "operator_confirmed": arm_confirmed,
            "armed_dry_run": arm_effective,
            "auto_disarmed": auto_disarmed,
            "readiness_passed": passed_gates,
            "readiness_total": len(readiness_gates),
            "master_execution_lock": not master_enabled,
            "master_enabled": master_enabled,
            "emergency_stop": emergency_stop,
            "would_execute": False,
        }

        # Supervised handover state machine (dry-run only).  This describes the
        # required ownership transition without changing Home Assistant, Modbus,
        # or the physical ELWA.  Changing the stored owner to Zeus is interpreted
        # only as a handover request while the master execution lock is ON.
        if not zeus_owner_selected:
            handover_phase = "HA_OWNS"
            handover_label = "Home Assistant owns ELWA"
            handover_next = "Select Zeus as the requested future owner when you are ready to begin a supervised handover."
        elif not previous_controller_off:
            handover_phase = "WAIT_HA_OFF"
            handover_label = "Waiting for Home Assistant controller OFF"
            handover_next = f"Disable {previous_controller_entity}; Zeus must observe OFF before handover can advance."
        elif not handover_confirmed:
            handover_phase = "WAIT_CONFIRMATION"
            handover_label = "Awaiting supervised handover confirmation"
            handover_next = "Confirm that Home Assistant control is disabled and Zeus would be the exclusive owner."
        elif not readiness_complete:
            handover_phase = "WAIT_READINESS"
            handover_label = "Ownership accepted; safety readiness incomplete"
            handover_next = "Resolve the remaining readiness gates before any execution arming step."
        elif not arm_effective:
            handover_phase = "READY_TO_ARM"
            handover_label = "Handover ready for supervised execution arming"
            handover_next = "All handover/readiness gates pass. Complete the execution-arm request and operator confirmation."
        elif not master_enabled:
            handover_phase = "ARMED_MASTER_OFF"
            handover_label = "Zeus ownership armed; execution master OFF"
            handover_next = "Arm is valid. Deliberately enable the execution master when real ELWA control is required."
        else:
            handover_phase = "ZEUS_READY"
            handover_label = "Zeus owns ELWA; supervised execution ready"
            handover_next = "Ownership, readiness, arm and master gates are satisfied. Runtime strategy and safety logic decide whether a write is needed."

        rollback_available = bool(previous_controller_entity and previous_controller_present)
        handover_steps = [
            {"id": "ha_owner", "label": "Home Assistant owns ELWA", "complete": zeus_owner_selected, "detail": "Normal operating state before handover."},
            {"id": "request_zeus", "label": "Request Zeus ownership", "complete": zeus_owner_selected, "detail": "Selecting Zeus only requests handover while execution is locked."},
            {"id": "ha_off", "label": "Verify existing HA controller OFF", "complete": bool(previous_controller_off), "detail": previous_controller_detail},
            {"id": "confirm", "label": "Confirm exclusive supervised handover", "complete": bool(zeus_owner_selected and handover_confirmed), "detail": "Operator confirmation is required after HA control is OFF."},
            {"id": "readiness", "label": "Verify all execution readiness gates", "complete": readiness_complete, "detail": f"{passed_gates}/{len(readiness_gates)} readiness gates pass."},
            {"id": "arm", "label": "Supervised execution arm", "complete": arm_effective, "detail": ("ARMED; real writes still require execution master enable." if arm_effective else f"{arm_state}; execution remains inactive.")},
        ]
        handover = {
            "phase": handover_phase,
            "label": handover_label,
            "next_action": handover_next,
            "control_owner": control_owner,
            "previous_controller_entity": previous_controller_entity,
            "previous_controller_state": previous_controller_state,
            "previous_controller_present": previous_controller_present,
            "previous_controller_off": previous_controller_off,
            "handover_confirmed": handover_confirmed,
            "rollback_available": rollback_available,
            "rollback_instruction": (f"Rollback path: return owner to Home Assistant, keep Zeus execution locked, and re-enable {previous_controller_entity}." if previous_controller_entity else "Rollback path requires the existing Home Assistant controller entity to be mapped."),
            "steps": handover_steps,
            "master_execution_lock": not master_enabled,
            "master_enabled": master_enabled,
            "would_execute": False,
        }

        runtime = dict(self._execution_runtime.get(device_id) or {})
        execution = {
            "capable_build": True,
            "master_enabled": master_enabled,
            "emergency_stop": emergency_stop,
            "strict_target_ok": strict_target_ok,
            "eligible": bool(readiness_complete and arm_effective and strict_target_ok and not emergency_stop),
            "active": bool(runtime.get("active", False)),
            "status": runtime.get("status", "MASTER_DISABLED" if not master_enabled else "READY_TO_EVALUATE"),
            "last_value_w": runtime.get("last_value_w"),
            "last_write_at": runtime.get("last_write_at"),
            "last_action": runtime.get("last_action"),
            "last_error": runtime.get("last_error"),
            "interlocks": list(runtime.get("interlocks") or []),
            "write_count": int(runtime.get("write_count", 0) or 0),
            "service": "zeus.direct_modbus" if direct_target_ready else "modbus.write_register",
            "target": {
                "transport": "direct" if direct_target_ready else "home_assistant",
                "host": direct_elwa_ip,
                "hub": command_hub,
                "unit": command_unit,
                "address": command_address,
                "temperature_address": 1001 if direct_target_ready else None,
            },
            "direct_modbus": {
                "configured": bool(direct_target_ready),
                "connected": bool(runtime.get("direct_connected")) if direct_target_ready else None,
                "status": runtime.get("direct_status") if direct_target_ready else None,
                "last_read_at": runtime.get("direct_last_read_at") if direct_target_ready else None,
                "power_w": runtime.get("direct_power_w") if direct_target_ready else None,
                "temperature_c": runtime.get("direct_temperature_c") if direct_target_ready else None,
                "last_error": runtime.get("direct_last_error") if direct_target_ready else None,
            },
        }

        return {
            "device_id": device.get("id"),
            "name": device.get("name") or device.get("id"),
            "mode": "simulation_only",
            "strategy": strategy,
            "decision": decision,
            "allowed": allowed,
            "requested_power_w": requested_w,
            "requested_power_kw": round(requested_w / 1000.0, 3),
            "actual_power_w": actual_power_w,
            "actual_power_kw": None if actual_power_w is None else round(actual_power_w / 1000.0, 3),
            "power_error_w": power_error_w,
            "power_error_percent": power_error_percent,
            "comparison": comparison,
            "reason": reason,
            "live": {
                "current_power_w": current_power_w,
                "surplus_w": surplus_w,
                "effective_solar_surplus_w": effective_solar_surplus_w,
                "solar_feedback_active": bool(solar_was_active and measured_elwa_for_solar_w > 0),
                "solar_feedback_elwa_power_w": measured_elwa_for_solar_w,
                "boiler_temperature_c": boiler_c,
                "element_temperature_c": element_c,
                "lockout_state": lockout_raw,
                "lockout_active": lockout_on,
                "grid_backup_active": grid_backup_active,
            },
            "limits": {
                "minimum_power_w": min_w,
                "maximum_power_w": max_w,
                "solar_maximum_power_w": solar_max_w,
                "grid_backup_power_w": grid_backup_power_w,
                "stop_temperature_c": stop_c,
                "restart_temperature_c": restart_c,
                "solar_start_threshold_w": solar_start_threshold_w,
                "solar_factor": solar_factor,
                "solar_export_reserve_w": solar_export_reserve_w,
                "element_taper_start_c": taper_start_c,
                "element_hard_stop_c": element_hard_stop_c,
                "grid_backup_start_c": grid_start_c,
                "grid_backup_stop_c": grid_stop_c,
                "keepalive_interval_s": policy["keepalive_interval_s"],
            },
            "profile_id": device.get("device_profile") or "my_pv_elwa_legacy_defaults",
            "command_preview": command_preview,
            "execution_readiness": execution_readiness,
            "handover": handover,
            "execution_arm": execution_arm,
            "execution": execution,
            "would_execute": bool(execution.get("active", False)),
        }


    async def _async_write_elwa_register(self, device_id: str, target: dict[str, Any], value_w: int, action: str) -> bool:
        """Write one validated ELWA register through direct or HA Modbus."""
        runtime = self._execution_runtime.setdefault(device_id, {})
        transport = str(target.get("transport") or "home_assistant")
        try:
            if transport == "direct":
                device = self._current_registry_device(device_id) or {}
                current_ip = self._direct_elwa_ip(device)
                if not device or str(device.get("device_profile") or "") != "my_pv_elwa":
                    raise ElwaModbusError("ELWA device/profile changed before write")
                safe_zero = int(value_w) == 0 and action in {
                    "EMERGENCY_STOP", "MASTER_DISABLED_STOP", "SAFETY_STOP",
                    "STOP", "INTEGRATION_UNLOAD_STOP",
                }
                if str(device.get("control_owner") or "").strip().lower() != "zeus":
                    raise ElwaModbusError("Zeus no longer owns ELWA control")
                if not safe_zero and (
                    not bool(device.get("enabled", True))
                    or not bool(device.get("controllable", False))
                    or not bool(device.get("control_permission", False))
                ):
                    raise ElwaModbusError("ELWA control permission changed before write")
                if not safe_zero and not bool(device.get("control_execution_master_enabled", False)):
                    raise ElwaModbusError("ELWA execution master is disabled")
                if current_ip != str(target.get("host") or "").strip():
                    raise ElwaModbusError("ELWA IP changed before write")
                client = self._direct_elwa_client(device)
                if client is None:
                    raise ElwaModbusError("ELWA Direct Modbus is not configured")
                await client.write_holding_register(int(target.get("address", 1000)), int(value_w))
                # Read back immediately.  This proves the TCP path is live and gives
                # diagnostics a fresh actual-power/temperature snapshot.
                snapshot = await client.read_snapshot()
                runtime.update(
                    direct_connected=True,
                    direct_status="CONNECTED",
                    direct_host=client.target.host,
                    direct_power_w=float(snapshot["power_w"]),
                    direct_temperature_c=float(snapshot["temperature_c"]),
                    direct_last_read_at=datetime.now(timezone.utc).isoformat(),
                    direct_last_error=None,
                )
            else:
                hass = getattr(self.registry, "hass", None)
                if hass is None:
                    raise RuntimeError("Home Assistant runtime unavailable")
                await hass.services.async_call(
                    "modbus",
                    "write_register",
                    {
                        "hub": str(target["hub"]),
                        "unit": int(target["unit"]),
                        "address": int(target["address"]),
                        "value": int(value_w),
                    },
                    blocking=True,
                )
        except Exception as err:
            runtime.update(
                status="WRITE_ERROR",
                last_error=str(err),
                active=False,
                last_action="ERROR",
            )
            if transport == "direct":
                runtime.update(direct_connected=False, direct_status="WRITE_ERROR", direct_last_error=str(err))
            try:
                self.event_bus.publish("SmartControlExecutionError", "SmartControlSafetyEngine", {"device_id": device_id, "error": str(err)})
            except Exception:
                pass
            return False
        now = datetime.now(timezone.utc)
        runtime.update(
            status="ACTIVE" if value_w > 0 else "SAFE_ZERO",
            active=True,
            last_value_w=int(value_w),
            last_write_at=now.isoformat(),
            last_write_dt=now,
            last_action=action,
            last_error=None,
            write_count=int(runtime.get("write_count", 0) or 0) + 1,
        )
        try:
            self.event_bus.publish(
                "SmartControlExecutionWrite",
                "SmartControlSafetyEngine",
                {"device_id": device_id, "value_w": int(value_w), "action": action, "target": dict(target)},
            )
        except Exception:
            pass
        return True

    def _current_registry_device(self, device_id: str) -> dict[str, Any] | None:
        """Return the current persisted registry device, never a scheduler snapshot."""
        for item in (self.registry.data or {}).get("devices", []):
            if isinstance(item, dict) and str(item.get("id") or "") == str(device_id):
                return item
        return None

    def _goe_publish_gate(self, device_id: str, topic: str | None = None) -> tuple[bool, str]:
        """Fail-closed permission check evaluated immediately before every MQTT write.

        The scheduler may have started with an older device snapshot while a user is
        revoking permission.  Never trust that snapshot for actuation: re-read the
        persisted registry immediately before mqtt.publish.
        """
        device = self._current_registry_device(device_id)
        if not device:
            return False, "Device no longer exists"
        if str(device.get("type") or "") != "ev_charger":
            return False, "Device is no longer an EV charger"
        if str(device.get("device_profile") or "") != "go_e_charger_mqtt":
            return False, "go-e MQTT profile is no longer selected"
        if not bool(device.get("enabled", True)):
            return False, "Device is disabled"
        if not bool(device.get("controllable", False)):
            return False, "Device is not marked controllable"
        if not bool(device.get("control_permission", False)):
            return False, "Zeus control permission is not granted"
        if not bool(device.get("control_dual_permission_armed", False)):
            return False, "go-e dual-permission hard gate is not armed"
        if str(device.get("actuator_type") or "") != "mqtt":
            return False, "MQTT actuator is not configured"
        if str(device.get("control_service") or "") != "mqtt.publish":
            return False, "mqtt.publish service is not configured"
        current_topic = str(device.get("control_mqtt_topic") or "").strip()
        if not current_topic:
            return False, "MQTT topic is not configured"
        if topic is not None and current_topic != str(topic):
            return False, "MQTT topic changed while publish was pending"
        return True, ""

    async def _async_publish_goe_ids(
        self,
        device_id: str,
        topic: str,
        pgrid_w: int,
        pakku_w: int,
        action: str = "PUBLISH",
    ) -> bool:
        """Publish one validated go-e IDS PV-surplus evidence payload via HA MQTT."""
        runtime = self._execution_runtime.setdefault(device_id, {})

        # alpha.6 safety hotfix: permission revocation is checked at the final
        # actuation boundary, immediately before Home Assistant mqtt.publish.
        # This closes the stale-scheduler/TOCTOU window seen during live testing.
        gate_ok, gate_reason = self._goe_publish_gate(device_id, topic)
        if not gate_ok:
            runtime.update(
                active=False,
                status="OBSERVE_ONLY",
                last_error=None,
                last_action="PERMISSION_REVOKED",
                last_write_dt=None,
            )
            try:
                self.event_bus.publish(
                    "SmartControlExecutionBlocked",
                    "SmartControlSafetyEngine",
                    {"device_id": device_id, "action": action, "reason": gate_reason},
                )
            except Exception:
                pass
            return False

        hass = getattr(self.registry, "hass", None)
        if hass is None:
            runtime.update(status="ERROR", last_error="Home Assistant runtime unavailable", active=False)
            return False

        payload_obj = {"pGrid": int(pgrid_w), "pAkku": int(pakku_w)}
        payload = json.dumps(payload_obj, separators=(",", ":"))
        try:
            await hass.services.async_call(
                "mqtt",
                "publish",
                {
                    "topic": str(topic),
                    "payload": payload,
                    "qos": 0,
                    "retain": False,
                },
                blocking=True,
            )
        except Exception as err:
            runtime.update(status="ERROR", last_error=str(err), active=False)
            return False

        now = datetime.now(timezone.utc)
        runtime.update(
            active=True,
            status="ACTIVE",
            last_write_dt=now,
            last_write_at=now.isoformat(),
            last_action=action,
            last_error=None,
            last_payload=payload_obj,
            last_topic=str(topic),
            write_count=int(runtime.get("write_count", 0) or 0) + 1,
        )
        try:
            self.event_bus.publish(
                "SmartControlExecutionWrite",
                "SmartControlSafetyEngine",
                {
                    "device_id": device_id,
                    "action": action,
                    "service": "mqtt.publish",
                    "topic": str(topic),
                    "payload": payload_obj,
                },
            )
        except Exception:
            pass
        return True

    async def _async_evaluate_goe_mqtt(self, registry_devices: dict[str, dict[str, Any]], force_keepalive: bool = False) -> None:
        """Publish go-e PV-surplus evidence only for explicitly permissioned devices.

        This intentionally mirrors the user's proven Home Assistant automation:
        pGrid is the rounded live grid-power sensor and pAkku is the optional
        battery-power sensor (or 0 when not configured). No current/amp command
        is calculated by Zeus; go-e's own IDS logic remains responsible for the
        charging decision.
        """
        now = datetime.now(timezone.utc)
        for device_id, device in registry_devices.items():
            if str(device.get("type") or "") != "ev_charger":
                continue
            if str(device.get("device_profile") or "") != "go_e_charger_mqtt":
                continue

            runtime = self._execution_runtime.setdefault(device_id, {})
            enabled = bool(device.get("enabled", True))
            controllable = bool(device.get("controllable", False))
            permission = bool(device.get("control_permission", False))
            dual_permission_armed = bool(device.get("control_dual_permission_armed", False))
            actuator_ok = (
                str(device.get("actuator_type") or "") == "mqtt"
                and str(device.get("control_service") or "") == "mqtt.publish"
            )
            topic = str(device.get("control_mqtt_topic") or "").strip()
            grid_entity = str(device.get("control_grid_power_entity") or "").strip()
            battery_entity = str(device.get("control_battery_power_entity") or "").strip()
            interval_s = int(device.get("control_publish_interval_s") or 5)
            interval_s = min(60, max(5, interval_s))

            # Fail closed: profile may be fully configured while still Observe Only.
            # In that state Zeus emits absolutely no MQTT traffic.
            if not (enabled and controllable and permission and dual_permission_armed):
                # Revocation is immediate and fail-closed. Clear the cadence
                # timestamp so a later deliberate re-enable publishes promptly.
                runtime.update(
                    active=False,
                    status="OBSERVE_ONLY",
                    last_error=None,
                    last_action="PERMISSION_REVOKED",
                    last_write_dt=None,
                )
                continue
            if not actuator_ok or not topic or not grid_entity:
                runtime.update(active=False, status="INTERLOCKED", last_error="Incomplete go-e MQTT target or grid-power mapping")
                continue

            pgrid = self._state_number(grid_entity)
            if pgrid is None:
                runtime.update(active=False, status="INTERLOCKED", last_error=f"Grid Power unavailable: {grid_entity}")
                continue
            if battery_entity:
                pakku = self._state_number(battery_entity)
                if pakku is None:
                    runtime.update(active=False, status="INTERLOCKED", last_error=f"Battery Power unavailable: {battery_entity}")
                    continue
            else:
                pakku = 0.0

            last_dt = runtime.get("last_write_dt")
            due = force_keepalive or last_dt is None or (now - last_dt).total_seconds() >= interval_s
            if not due:
                runtime.update(active=True, status="ACTIVE", last_error=None)
                continue

            await self._async_publish_goe_ids(
                device_id,
                topic,
                int(round(pgrid)),
                int(round(pakku)),
                "PUBLISH",
            )

    async def async_evaluate_execution(self, *_args, force_keepalive: bool = False) -> None:
        """Recompute safety and, when explicitly armed, execute the ELWA Modbus command.

        This is intentionally limited to profile my_pv_elwa and the validated
        ELWA/unit-1/address-1000 target.  The master enable defaults OFF.
        """
        registry_devices = {str(d.get("id")): d for d in (self.registry.data or {}).get("devices", []) if isinstance(d, dict)}
        await self._async_poll_all_direct_elwa(registry_devices)
        self.refresh()

        # go-e IDS evidence publishing is a separate supervised transport. It is
        # passive until both per-device control checkboxes are explicitly enabled.
        await self._async_evaluate_goe_mqtt(registry_devices, force_keepalive=force_keepalive)

        for simulation in list(self.data.get("simulations") or []):
            device_id = str(simulation.get("device_id") or "")
            device = registry_devices.get(device_id)
            if not device or str(device.get("device_profile") or "") != "my_pv_elwa":
                continue
            runtime = self._execution_runtime.setdefault(device_id, {})
            execution = simulation.get("execution") or {}
            readiness = simulation.get("execution_readiness") or {}
            arm = simulation.get("execution_arm") or {}
            handover = simulation.get("handover") or {}
            target = execution.get("target") or {}
            master_enabled = bool(device.get("control_execution_master_enabled", False))
            emergency_stop = bool(device.get("control_emergency_stop", False))
            strict_target_ok = bool(execution.get("strict_target_ok"))
            all_ready = bool(readiness.get("ready_except_master_lock"))
            armed = bool(arm.get("armed_dry_run"))
            owner_zeus = str(handover.get("control_owner") or "") == "zeus"
            # Use the handover evaluator's portable exclusivity result.  A device
            # with no configured previous HA controller is safe here because there
            # is no competing writer to disable.
            previous_off = bool(handover.get("previous_controller_off"))

            # Emergency stop has highest priority. It may emit one safe zero only
            # while Zeus still has exclusive ownership and the previous HA writer
            # is OFF. It never writes into an HA-owned/shared-control state.
            if emergency_stop:
                if (runtime.get("active") or master_enabled) and owner_zeus and previous_off and strict_target_ok and runtime.get("last_value_w") != 0:
                    await self._async_write_elwa_register(device_id, target, 0, "EMERGENCY_STOP")
                runtime.update(active=False, status="EMERGENCY_STOP")
                continue

            # Turning the master OFF while Zeus is actively driving ELWA performs
            # a one-shot safe zero before becoming passive, provided exclusivity
            # is still intact. On upgrade/startup master defaults OFF, so no write
            # occurs merely because this build was installed.
            if not master_enabled:
                if runtime.get("active") and owner_zeus and previous_off and strict_target_ok and runtime.get("last_value_w") != 0:
                    await self._async_write_elwa_register(device_id, target, 0, "MASTER_DISABLED_STOP")
                runtime.update(active=False, status="MASTER_DISABLED", last_error=None, interlocks=[])
                continue

            execution_allowed = bool(all_ready and armed and owner_zeus and previous_off and strict_target_ok)
            if not execution_allowed:
                # Preserve the exact supervised gate evidence that blocked real
                # execution.  v14.8.6-alpha.7 exposed INTERLOCKED but not the
                # reason, which made a valid 2 kW request impossible to diagnose.
                interlocks: list[str] = []
                if not all_ready:
                    failed = [
                        gate for gate in (readiness.get("gates") or [])
                        if isinstance(gate, dict) and not bool(gate.get("passed"))
                    ]
                    if failed:
                        for gate in failed:
                            label = str(gate.get("label") or gate.get("id") or "Readiness gate")
                            detail = str(gate.get("detail") or "").strip()
                            interlocks.append(f"{label}: {detail}" if detail else label)
                    else:
                        interlocks.append("Execution readiness is incomplete")
                elif not armed:
                    arm_state = str(arm.get("state") or "DISARMED")
                    arm_next = str(arm.get("next_action") or "Execution arm is not confirmed").strip()
                    interlocks.append(f"Execution arm {arm_state}: {arm_next}")
                if not owner_zeus:
                    interlocks.append(f"Control owner is {handover.get('control_owner') or 'not Zeus'}")
                if not previous_off:
                    previous_entity = handover.get("previous_controller_entity") or "previous Home Assistant controller"
                    previous_state = handover.get("previous_controller_state") or "unknown"
                    interlocks.append(f"{previous_entity} is {previous_state}; it must be OFF")
                if not strict_target_ok:
                    interlocks.append("ELWA execution target is not the validated unit 1 / register 1000 target")
                interlock_reason = "; ".join(dict.fromkeys(interlocks)) or "Supervised execution gate is not satisfied"

                # If Zeus was actively driving the device and a non-ownership
                # safety gate fails while HA remains OFF, fail closed with 0 W.
                if runtime.get("active") and owner_zeus and previous_off and strict_target_ok and runtime.get("last_value_w") != 0:
                    await self._async_write_elwa_register(device_id, target, 0, "SAFETY_STOP")
                runtime.update(active=False, status="INTERLOCKED", last_error=interlock_reason, interlocks=interlocks)
                continue

            desired_w = int(max(0, round(float(simulation.get("requested_power_w") or 0))))
            max_w = int(round(float((simulation.get("limits") or {}).get("maximum_power_w") or 0)))
            min_w = int(round(float((simulation.get("limits") or {}).get("minimum_power_w") or 0)))
            desired_w = min(desired_w, max_w)
            if 0 < desired_w < min_w:
                desired_w = 0

            interval_s = int((simulation.get("limits") or {}).get("keepalive_interval_s") or 30)
            now = datetime.now(timezone.utc)
            last_dt = runtime.get("last_write_dt")
            last_value = runtime.get("last_value_w")
            due = force_keepalive or last_dt is None or (now - last_dt).total_seconds() >= interval_s
            # Dynamic solar modulation is evaluated every 5 s, but do not
            # generate Modbus traffic for tiny sensor wobble. Non-zero command
            # changes of >=100 W are applied immediately; smaller drift is
            # absorbed until the configured keepalive, when the newest safe target is
            # sent. Start/stop transitions always remain immediate.
            changed = last_value != desired_w
            meaningful_change = (
                last_value is None
                or (last_value == 0) != (desired_w == 0)
                or abs(int(last_value or 0) - desired_w) >= 100
            )

            # ELWA only needs keepalive while Zeus is requesting non-zero power.
            # A zero request is edge-triggered: write 0 once when stopping an
            # active/non-zero Zeus request, then remain passive while idle.
            # If Zeus has not issued a non-zero request in this runtime, an
            # already-idle 0 W decision does not generate periodic Modbus writes.
            if desired_w == 0:
                if runtime.get("active") and last_value not in (None, 0):
                    ok = await self._async_write_elwa_register(device_id, target, 0, "STOP")
                    if not ok:
                        runtime["active"] = False
                else:
                    runtime.update(
                        active=False,
                        status="IDLE",
                        last_action="IDLE_NO_WRITE",
                        last_error=None,
                        interlocks=[],
                    )
                continue

            # Non-zero requests are written immediately on change and refreshed
            # at the configured keepalive interval so ELWA keeps the requested
            # power value.
            if meaningful_change or due or not runtime.get("active"):
                action = "WRITE" if meaningful_change or last_value is None else "KEEPALIVE"
                ok = await self._async_write_elwa_register(device_id, target, desired_w, action)
                if not ok:
                    runtime["active"] = False
            else:
                runtime.update(active=True, status="ACTIVE", last_error=None, interlocks=[])

        # Refresh once more so the dashboard immediately reports runtime outcome.
        self.refresh()

    async def async_shutdown_execution(self) -> None:
        """Best-effort safe zero for active supervised ELWA sessions on unload."""
        registry_devices = {str(d.get("id")): d for d in (self.registry.data or {}).get("devices", []) if isinstance(d, dict)}
        for device_id, runtime in list(self._execution_runtime.items()):
            if not runtime.get("active") or runtime.get("last_value_w") in (None, 0):
                continue
            device = registry_devices.get(str(device_id)) or {}
            if str(device.get("control_owner") or "").lower() != "zeus":
                continue
            previous = str(device.get("control_previous_controller_entity") or "").strip() or None
            previous_state = self._state_text(previous) if previous else None
            if previous is not None and str(previous_state or "").lower() != "off":
                continue
            direct_ip = self._direct_elwa_ip(device)
            target = {
                "transport": "direct" if direct_ip else "home_assistant",
                "host": direct_ip,
                "hub": device.get("control_hub"),
                "unit": 1 if direct_ip else device.get("control_unit"),
                "address": 1000 if direct_ip else device.get("control_address"),
            }
            strict = (str(target.get("hub") or "").lower() == "elwa" and target.get("unit") == 1 and target.get("address") == 1000)
            if strict:
                await self._async_write_elwa_register(str(device_id), target, 0, "INTEGRATION_UNLOAD_STOP")
            runtime.update(active=False, status="UNLOADED")


    def _capture_simulation_transition(self, simulation: dict[str, Any]) -> None:
        """Capture only meaningful simulator changes for later review."""
        device_id = str(simulation.get("device_id") or "unknown")
        requested_w = int(round(float(simulation.get("requested_power_w") or 0)))
        live = simulation.get("live") or {}
        # Round live evidence to avoid logging every tiny sensor wobble.
        signature = (
            str(simulation.get("decision") or ""),
            str(simulation.get("strategy") or "none"),
            requested_w // 100 * 100,
            bool(live.get("lockout_active")),
            str(simulation.get("reason") or "").split(".")[0],
        )
        previous = self._last_simulation_signature.get(device_id)

        # Always capture first observation; afterwards only meaningful state/reason
        # changes or >=100 W requested-power band changes.
        if previous == signature:
            return

        self._last_simulation_signature[device_id] = signature
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "device_id": device_id,
            "name": simulation.get("name") or device_id,
            "decision": simulation.get("decision") or "UNKNOWN",
            "strategy": simulation.get("strategy") or "none",
            "requested_power_w": requested_w,
            "requested_power_kw": round(requested_w / 1000.0, 3),
            "reason": simulation.get("reason"),
            "current_power_w": live.get("current_power_w"),
            "actual_power_w": simulation.get("actual_power_w"),
            "power_error_w": simulation.get("power_error_w"),
            "power_error_percent": simulation.get("power_error_percent"),
            "comparison": simulation.get("comparison"),
            "surplus_w": live.get("surplus_w"),
            "boiler_temperature_c": live.get("boiler_temperature_c"),
            "element_temperature_c": live.get("element_temperature_c"),
            "lockout_active": bool(live.get("lockout_active")),
            "would_execute": False,
        }
        self._simulation_history.append(row)
        # Keep current-day / practical review payload bounded.
        self._simulation_history = self._simulation_history[-120:]

        try:
            self.event_bus.publish(
                "SmartControlSimulationTransition",
                "SmartControlSafetyEngine",
                row,
            )
        except Exception:
            pass

    def refresh(self) -> None:
        registry_data = self.registry.data or {}
        plugin_settings = registry_data.get("plugin_settings") or {}
        global_mode = str(plugin_settings.get("safety_mode") or "recommendation_only")

        devices = [
            self._device_capability(device, global_mode)
            for device in registry_data.get("devices", [])
            if isinstance(device, dict)
        ]
        candidates = [d for d in devices if d["controllable"] and d["enabled"]]
        permissioned = [d for d in candidates if d["control_permission"]]

        simulations: list[dict[str, Any]] = []
        by_id = {str(d.get("device_id")): d for d in devices}
        for device in registry_data.get("devices", []):
            if not isinstance(device, dict):
                continue
            if str(device.get("type") or "") == "water_heater" and bool(device.get("controllable", False)):
                capability = by_id.get(str(device.get("id")), {})
                simulation = self._simulate_water_heater(device, capability)
                simulations.append(simulation)
                self._capture_simulation_transition(simulation)

        latest_transition = self._simulation_history[-1] if self._simulation_history else None

        goe_mqtt_devices = []
        for device in registry_data.get("devices", []):
            if not isinstance(device, dict) or str(device.get("device_profile") or "") != "go_e_charger_mqtt":
                continue
            device_id = str(device.get("id") or "")
            runtime = dict(self._execution_runtime.get(device_id) or {})
            goe_mqtt_devices.append({
                "device_id": device_id,
                "name": device.get("name") or device_id,
                "charger_id": device.get("control_goe_id"),
                "topic": device.get("control_mqtt_topic"),
                "grid_power_entity": device.get("control_grid_power_entity"),
                "battery_power_entity": device.get("control_battery_power_entity"),
                "publish_interval_s": int(device.get("control_publish_interval_s") or 5),
                "controllable": bool(device.get("controllable", False)),
                "control_permission": bool(device.get("control_permission", False)),
                "active": bool(runtime.get("active", False)),
                "status": runtime.get("status", "OBSERVE_ONLY"),
                "last_publish_at": runtime.get("last_write_at"),
                "last_payload": runtime.get("last_payload"),
                "last_topic": runtime.get("last_topic"),
                "last_error": runtime.get("last_error"),
                "publish_count": int(runtime.get("write_count", 0) or 0),
            })

        self.data = {
            "status": "Ready",
            "foundation_version": self.VERSION,
            "mode": global_mode,
            "execution_path": "supervised_elwa_modbus+goe_mqtt_ids",
            "automatic_control_enabled": False,
            "supervised_control_enabled": any(bool((sim.get("execution") or {}).get("active")) for sim in simulations),
            "recommendation_only": True,
            "fail_closed": True,
            "registered_devices": len(devices),
            "controllable_candidates": len(candidates),
            "permissioned_candidates": len(permissioned),
            "devices": devices,
            "simulations": simulations,
            "simulation_count": len(simulations),
            "simulation_history": list(reversed(self._simulation_history[-40:])),
            "simulation_history_count": len(self._simulation_history),
            "latest_simulation_transition": latest_transition,
            "goe_mqtt": {
                "devices": goe_mqtt_devices,
                "configured": len(goe_mqtt_devices),
                "active": sum(1 for row in goe_mqtt_devices if row.get("active")),
            },
            "safety_principles": [
                "Explicit per-device permission is required.",
                "Missing or invalid evidence must block control.",
                "Configured actuator limits must be enforced before execution.",
                "Global Recommendation Only mode overrides device permissions.",
                "Every future actuation must be auditable.",
                "Only the supervised my-PV ELWA path can call modbus.write_register, and only after every execution gate passes.",
                "go-e Charger IDS MQTT publishing is fail-closed and requires explicit controllable + Zeus control permission on each charger.",
                "The go-e path publishes only pGrid/pAkku evidence; go-e remains responsible for its own PV-surplus charging algorithm.",
            ],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def summary(self) -> dict[str, Any]:
        return dict(self.data)
