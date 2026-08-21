"""Compatibility exports for AION EMS Zeus Knowledge Engine 2.0.

Import symbols from their concrete modules instead of relying on package-level
re-exports. This remains robust when Home Assistant encounters a stale or
partially copied ``knowledge_v2_pkg`` directory during an upgrade.
"""
from .knowledge_v2_pkg.knowledge_engine import KnowledgeEngineV2
from .knowledge_v2_pkg.learning_intelligence import LearningIntelligenceV2
from .knowledge_v2_pkg.timeline_engine import KnowledgeTimelineEngine
from .knowledge_v2_pkg.executive_briefing import ExecutiveBriefingEngine
from .knowledge_v2_pkg.advisor_v2 import AdvisorV2
from .knowledge_v2_pkg.decision_engine import DecisionEngine
from .knowledge_v2_pkg.intelligence_memory import IntelligenceMemoryEngine
from .knowledge_v2_pkg.scenario_simulator import ScenarioSimulator
from .knowledge_v2_pkg.prediction_accuracy import PredictionAccuracyEngine
from .knowledge_v2_pkg.home_profile import HomeProfileEngine
from .knowledge_v2_pkg.anomaly_engine import AnomalyIntelligenceEngine
from .knowledge_v2_pkg.opportunity_learning import OpportunityLearningEngine
from .knowledge_v2_pkg.adaptive_advisor import AdaptiveAdvisorEngine
from .knowledge_v2_pkg.intelligence_fusion import IntelligenceFusionEngine

__all__ = [
    "KnowledgeEngineV2",
    "LearningIntelligenceV2",
    "KnowledgeTimelineEngine",
    "ExecutiveBriefingEngine",
    "AdvisorV2",
    "DecisionEngine",
    "IntelligenceMemoryEngine",
    "ScenarioSimulator",
    "PredictionAccuracyEngine",
    "HomeProfileEngine",
    "AnomalyIntelligenceEngine",
    "OpportunityLearningEngine",
    "AdaptiveAdvisorEngine",
    "IntelligenceFusionEngine",
]
