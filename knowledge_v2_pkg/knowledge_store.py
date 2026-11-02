"""Small in-memory knowledge store for the alpha foundation."""

from __future__ import annotations

from collections import deque
from typing import Any


class KnowledgeStore:
    def __init__(self, max_entries: int = 96) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)

    def append(self, entry: dict[str, Any]) -> None:
        self._entries.append(entry)

    def summary(self) -> dict[str, Any]:
        latest = self._entries[-1] if self._entries else None
        return {
            "entry_count": len(self._entries),
            "capacity": self._entries.maxlen,
            "latest_generated_at": latest.get("generated_at") if latest else None,
        }
