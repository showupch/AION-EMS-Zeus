"""Build a Recorder-safe relationship graph for Zeus Knowledge Engine 2.0."""

from __future__ import annotations

from typing import Any


class KnowledgeGraphBuilder:
    NODE_ORDER = ("weather", "forecast", "solar", "battery", "home", "grid", "finance", "learning", "advisor")

    def build(self, context: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
        energy = context.get("energy", {})
        available = {item.get("source"): bool(item.get("available")) for item in evidence}
        nodes = [
            {"id": "weather", "label": "Weather", "active": available.get("Forecast", False)},
            {"id": "forecast", "label": "Forecast", "active": available.get("Forecast", False)},
            {"id": "solar", "label": "Solar", "active": energy.get("solar_w") is not None},
            {"id": "battery", "label": "Battery", "active": energy.get("battery_soc_percent") is not None},
            {"id": "home", "label": "Home", "active": energy.get("home_w") is not None},
            {"id": "grid", "label": "Grid", "active": energy.get("grid_import_w") is not None or energy.get("grid_export_w") is not None},
            {"id": "finance", "label": "Finance", "active": available.get("Finance", False)},
            {"id": "learning", "label": "Learning", "active": available.get("Learning", False)},
            {"id": "advisor", "label": "Advisor", "active": True},
        ]
        edges = [
            {"from": "weather", "to": "forecast", "type": "influences"},
            {"from": "forecast", "to": "solar", "type": "predicts"},
            {"from": "solar", "to": "battery", "type": "charges"},
            {"from": "solar", "to": "home", "type": "supplies"},
            {"from": "battery", "to": "home", "type": "supports"},
            {"from": "home", "to": "grid", "type": "balances"},
            {"from": "grid", "to": "finance", "type": "values"},
            {"from": "learning", "to": "advisor", "type": "supports"},
            {"from": "forecast", "to": "advisor", "type": "supports"},
            {"from": "finance", "to": "advisor", "type": "supports"},
        ]
        return {
            "node_count": len(nodes),
            "active_node_count": sum(1 for node in nodes if node["active"]),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
        }
