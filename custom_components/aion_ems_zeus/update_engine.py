"""Event-driven update engine for AION EMS Zeus."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)


class UpdateEngine:
    """Refresh Zeus when mapped Home Assistant source entities change."""

    DEBOUNCE_SECONDS = 5.0
    MIN_REFRESH_SECONDS = 5.0
    SAFETY_REFRESH_SECONDS = 900

    def __init__(
        self,
        hass: HomeAssistant,
        event_bus,
        tracked_entities_fn: Callable[[], set[str]],
        refresh_fn: Callable[[], None],
    ) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self._tracked_entities_fn = tracked_entities_fn
        self._refresh_fn = refresh_fn
        self._listeners: list[Callable[[], Any]] = []
        self._unsub_state = None
        self._unsub_safety = None
        self._unsub_debounce = None
        self._pending_entity: str | None = None
        self._pending_source_changed: datetime | None = None
        self._tracked_entities: set[str] = set()
        self._last_refresh_started: datetime | None = None
        self._refresh_in_progress = False
        self.last: dict[str, Any] = {
            "status": "Stopped",
            "mode": "event_driven",
            "debounce_seconds": self.DEBOUNCE_SECONDS,
            "safety_refresh_seconds": self.SAFETY_REFRESH_SECONDS,
            "minimum_refresh_seconds": self.MIN_REFRESH_SECONDS,
            "tracked_entity_count": 0,
            "processed_updates": 0,
            "source_events": 0,
            "safety_refreshes": 0,
            "errors": 0,
        }

    async def async_start(self) -> None:
        self.refresh_tracked_entities()
        if self._unsub_safety is None:
            self._unsub_safety = async_track_time_interval(
                self.hass,
                self._async_safety_refresh,
                timedelta(seconds=self.SAFETY_REFRESH_SECONDS),
            )
        self.last["status"] = "Running"
        self.last["started_at"] = self._now()
        self._update_tracking_stats()

    def refresh_tracked_entities(self) -> None:
        """Subscribe only to mapped Zeus source entities, not every HA state event."""
        tracked = set(self._tracked_entities_fn())
        if tracked == self._tracked_entities and self._unsub_state is not None:
            return
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        self._tracked_entities = tracked
        if tracked:
            self._unsub_state = async_track_state_change_event(
                self.hass, sorted(tracked), self._handle_state_changed
            )
        self._update_tracking_stats()

    async def async_stop(self) -> None:
        for attr in ("_unsub_state", "_unsub_safety", "_unsub_debounce"):
            unsub = getattr(self, attr)
            if unsub is not None:
                unsub()
                setattr(self, attr, None)
        self.last["status"] = "Stopped"

    def add_listener(self, callback: Callable[[], Any]) -> Callable[[], None]:
        self._listeners.append(callback)

        def _remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return _remove

    def _handle_state_changed(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        if not entity_id or entity_id not in self._tracked_entities:
            return

        self.last["source_events"] = self.last.get("source_events", 0) + 1
        self.last["last_source_entity"] = entity_id
        self.last["last_source_event"] = self._now()
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        # Home Assistant may emit state_changed events when nothing material changed.
        # Ignore those so mapped sensors cannot trigger needless full Zeus refreshes.
        if old_state is not None and new_state is not None:
            if old_state.state == new_state.state and old_state.attributes == new_state.attributes:
                return
        self._pending_entity = entity_id
        self._pending_source_changed = getattr(new_state, "last_updated", None)
        self._update_tracking_stats()

        if self._unsub_debounce is None:
            self._unsub_debounce = async_call_later(
                self.hass, self.DEBOUNCE_SECONDS, self._debounced_refresh
            )

    async def _debounced_refresh(self, _now=None) -> None:
        self._unsub_debounce = None
        await self._async_refresh("source_event", self._pending_entity)
        self._pending_entity = None
        self._pending_source_changed = None

    async def _async_safety_refresh(self, _now=None) -> None:
        self.last["safety_refreshes"] = self.last.get("safety_refreshes", 0) + 1
        self.refresh_tracked_entities()
        await self._async_refresh("safety_refresh", None)

    async def _async_refresh(self, reason: str, entity_id: str | None) -> None:
        started = datetime.now(timezone.utc)
        if self._refresh_in_progress:
            self.last["coalesced_refreshes"] = self.last.get("coalesced_refreshes", 0) + 1
            return
        if self._last_refresh_started is not None:
            elapsed = (started - self._last_refresh_started).total_seconds()
            if elapsed < self.MIN_REFRESH_SECONDS and reason != "safety_refresh":
                self.last["rate_limited_refreshes"] = self.last.get("rate_limited_refreshes", 0) + 1
                return
        self._refresh_in_progress = True
        self._last_refresh_started = started
        try:
            self._refresh_fn()
            completed = datetime.now(timezone.utc)
            self.last["processed_updates"] = self.last.get("processed_updates", 0) + 1
            self.last["last_refresh_reason"] = reason
            self.last["last_refreshed"] = completed.isoformat()
            self.last["last_refresh_duration_ms"] = round(
                (completed - started).total_seconds() * 1000, 1
            )
            if entity_id:
                self.last["last_processed_entity"] = entity_id
            if self._pending_source_changed:
                source_changed = self._pending_source_changed
                if source_changed.tzinfo is None:
                    source_changed = source_changed.replace(tzinfo=timezone.utc)
                self.last["update_latency_ms"] = max(
                    0,
                    round((completed - source_changed).total_seconds() * 1000, 1),
                )
            self.last["status"] = "Running"
            self._update_tracking_stats()
            self.event_bus.publish(
                "UpdateEngineRefreshed",
                "UpdateEngine",
                {"reason": reason, "entity_id": entity_id},
            )
            for listener in list(self._listeners):
                result = listener()
                if hasattr(result, "__await__"):
                    await result
            self._refresh_in_progress = False
        except Exception as err:  # Home Assistant should keep running on bad input.
            self._refresh_in_progress = False
            self.last["errors"] = self.last.get("errors", 0) + 1
            self.last["status"] = "Error"
            self.last["last_error"] = str(err)
            self.event_bus.publish(
                "UpdateEngineFailed", "UpdateEngine", {"error": str(err)}
            )

    def _update_tracking_stats(self) -> None:
        self.last["tracked_entity_count"] = len(self._tracked_entities)
        self.last["tracked_entities"] = sorted(self._tracked_entities)

    def summary(self) -> dict[str, Any]:
        self._update_tracking_stats()
        return dict(self.last)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
