"""Persistent supervised-control audit trail for Zeus."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
import asyncio

CONTROL_EVENTS = {
    "SmartControlExecutionWrite": "Executed",
    "SmartControlExecutionBlocked": "Blocked",
    "SmartControlExecutionError": "Failed",
}

class ControlAuditTrail:
    """Record bounded, persistent evidence for Zeus supervised-control actions."""

    MAX_ENTRIES = 200

    def __init__(self, registry) -> None:
        self.registry = registry
        self._save_pending = False

    def _entries(self) -> list[dict[str, Any]]:
        block = self.registry.data.setdefault("control_audit", [])
        if not isinstance(block, list):
            block = []
        # v16.0.138 migration: successful go-e PUBLISH_5S records were routine
        # telemetry, not meaningful control decisions. Purge legacy cards so
        # existing installations become clean immediately after update.
        filtered = [
            item for item in block
            if not (
                isinstance(item, dict)
                and str(item.get("result") or "") == "Executed"
                and str(item.get("action") or "") == "PUBLISH_5S"
            )
        ]
        if len(filtered) != len(block):
            self.registry.data["control_audit"] = filtered
            self._schedule_save()
        else:
            self.registry.data["control_audit"] = block
        return self.registry.data["control_audit"]

    def ingest_event(self, record: dict[str, Any]) -> None:
        event = str(record.get("event") or "")
        if event not in CONTROL_EVENTS:
            return
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        action = str(payload.get("action") or "")
        # go-e PUBLISH_5S is a continuous IDS evidence feed, not a control decision.
        # Successful routine publishes are represented by one compact per-device
        # telemetry status and never consume persistent audit entries. Blocked and
        # failed attempts remain auditable.
        if event == "SmartControlExecutionWrite" and action == "PUBLISH_5S":
            status = self.registry.data.setdefault("control_audit_telemetry", {})
            if not isinstance(status, dict):
                status = {}
                self.registry.data["control_audit_telemetry"] = status
            device_id = str(payload.get("device_id") or "unknown")
            status[device_id] = {
                "timestamp": record.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                "service": payload.get("service"),
                "topic": payload.get("topic"),
                "evidence": payload.get("payload") if isinstance(payload.get("payload"), dict) else None,
                "status": "Active",
            }
            # No persistent save every five seconds: this is live telemetry status.
            return
        entry = {
            "timestamp": record.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "result": CONTROL_EVENTS[event],
            "event": event,
            "module": str(record.get("engine") or "Zeus"),
            "device_id": payload.get("device_id"),
            "action": payload.get("action"),
            "service": payload.get("service"),
            "reason": payload.get("reason"),
            "topic": payload.get("topic"),
            "evidence": payload.get("payload") if isinstance(payload.get("payload"), dict) else None,
        }
        entries = self._entries()
        # Suppress an identical repeated decision within 60 seconds. Never suppress
        # failures/blocks because each is operationally meaningful.
        if entry["result"] == "Executed" and entries:
            previous = entries[-1]
            if (
                previous.get("result") == entry["result"]
                and previous.get("device_id") == entry["device_id"]
                and previous.get("action") == entry["action"]
                and previous.get("service") == entry["service"]
                and previous.get("evidence") == entry["evidence"]
            ):
                try:
                    a = datetime.fromisoformat(str(previous.get("timestamp")).replace("Z", "+00:00"))
                    b = datetime.fromisoformat(str(entry.get("timestamp")).replace("Z", "+00:00"))
                    if abs((b - a).total_seconds()) < 60:
                        return
                except Exception:
                    pass
        entries.append(entry)
        self.registry.data["control_audit"] = entries[-self.MAX_ENTRIES:]
        self._schedule_save()

    def record_manual(self, *, module: str, device_id: str | None, action: str,
                      previous: Any = None, new: Any = None, reason: str | None = None,
                      authority: str | None = None, result: str = "Successful") -> None:
        entries = self._entries()
        entries.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": result,
            "event": "ManualSupervisedControl",
            "module": module,
            "device_id": device_id,
            "action": action,
            "previous": previous,
            "new": new,
            "reason": reason,
            "authority": authority,
        })
        self.registry.data["control_audit"] = entries[-self.MAX_ENTRIES:]
        self._schedule_save()

    def _schedule_save(self) -> None:
        if self._save_pending:
            return
        self._save_pending = True
        async def _save():
            try:
                await self.registry.async_save()
            finally:
                self._save_pending = False
        try:
            asyncio.get_running_loop().create_task(_save())
        except RuntimeError:
            self._save_pending = False

    def summary(self) -> dict[str, Any]:
        entries = list(reversed(self._entries()))
        telemetry = self.registry.data.get("control_audit_telemetry", {})
        return {
            "status": "Ready",
            "count": len(entries),
            "entries": entries[:25],
            "visible_limit": 25,
            "retained_entries": min(len(entries), self.MAX_ENTRIES),
            "max_entries": self.MAX_ENTRIES,
            "telemetry": telemetry if isinstance(telemetry, dict) else {},
            "principle": "Meaningful supervised-control decisions are audited; routine IDS telemetry is summarized separately.",
        }
