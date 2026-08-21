"""AION EMS Zeus Knowledge Engine 2.0 alpha foundation."""

from __future__ import annotations

from typing import Any

from .context_builder import ContextBuilder
from .evidence_builder import EvidenceBuilder
from .explanation_engine import ExplanationEngine
from .knowledge_graph import KnowledgeGraphBuilder
from .knowledge_store import KnowledgeStore
from .seasonal_memory import SeasonalMemory
from .similarity_engine import SimilarityEngine


class KnowledgeEngineV2:
    """Create explainable, evidence-backed knowledge without device control."""

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self.context_builder = ContextBuilder()
        self.evidence_builder = EvidenceBuilder()
        self.graph_builder = KnowledgeGraphBuilder()
        self.explanation_engine = ExplanationEngine()
        self.similarity_engine = SimilarityEngine()
        self.seasonal_memory = SeasonalMemory()
        self.store = KnowledgeStore()
        self.last: dict[str, Any] = {
            "status": "Not ready",
            "version": "2.0-beta.1",
            "confidence": 0,
            "summary": "Knowledge Engine 2.0 is waiting for the first refresh.",
        }

    def refresh(self) -> dict[str, Any]:
        context = self.context_builder.build(self.core)
        evidence = self.evidence_builder.build(context)
        confidence = self.evidence_builder.confidence(evidence)
        graph = self.graph_builder.build(context, evidence)
        explanation = self.explanation_engine.build(context, evidence, confidence)
        similarity = self.similarity_engine.build(context, self.core)
        seasonal = self.seasonal_memory.build(context)
        record = {
            "generated_at": context.get("generated_at"),
            "confidence": confidence,
            "recommendation": explanation.get("recommendation"),
        }
        self.store.append(record)
        self.last = {
            "status": "Ready",
            "version": "2.0-beta.1",
            "confidence": confidence,
            "confidence_label": "High" if confidence >= 80 else "Medium" if confidence >= 55 else "Learning",
            "recommendation": explanation.get("recommendation"),
            "why": explanation.get("why"),
            "evidence": evidence,
            "explanation": explanation,
            "graph": graph,
            "similarity": similarity,
            "seasonal_memory": seasonal,
            "learning_intelligence": self.core.learning_intelligence_v2.summary(),
            "store": self.store.summary(),
            "summary": f"Knowledge Engine 2.0 combined {sum(1 for item in evidence if item.get('available'))} active evidence source(s).",
            "safety": "Recommendation only. Autonomous control remains disabled.",
            "recorder_safe": True,
        }
        try:
            self.event_bus.publish(
                "KnowledgeV2Updated",
                "KnowledgeEngineV2",
                {"confidence": confidence, "active_nodes": graph.get("active_node_count", 0)},
            )
        except Exception:
            pass
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
