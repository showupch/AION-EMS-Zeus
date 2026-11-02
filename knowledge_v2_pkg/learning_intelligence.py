"""Learning & similar-day intelligence orchestration."""
from __future__ import annotations
from typing import Any
from .behavior_learning import BehaviorLearning
from .pattern_detector import PatternDetector
from .similarity_engine import SimilarityEngine
from .confidence_model import ConfidenceModel


class LearningIntelligenceV2:
    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus=event_bus; self.core=core
        self.behavior=BehaviorLearning(); self.detector=PatternDetector(); self.similarity_engine=SimilarityEngine(); self.confidence_model=ConfidenceModel()
        self.last={"status":"Waiting","version":"2.0-beta.1","summary":"Collecting measured daily history."}

    def refresh(self) -> dict[str, Any]:
        behavior=self.behavior.build(self.core)
        patterns=self.detector.build(behavior)
        similarity=self.similarity_engine.build({}, self.core)
        quality=getattr(getattr(self.core,"data_quality",None),"summary",lambda:{})().get("confidence_score")
        confidence=self.confidence_model.build(behavior, similarity, quality)
        primary=(patterns.get("patterns") or [None])[0]
        insight = similarity.get("message") if similarity.get("similar_day") else (f"{primary.get('label')} is currently the strongest learned {primary.get('type','pattern').replace('_',' ')}." if primary else behavior.get("message"))
        self.last={"status":"Ready" if behavior.get("history_days",0)>=3 else "Learning","version":"2.0-beta.1",
                   "history_days":behavior.get("history_days",0),"confidence_percent":confidence["score"],"confidence_label":confidence["label"],
                   "similar_day":similarity.get("similar_day"),"similarity_percent":similarity.get("similarity_percent"),
                   "similar_day_message":similarity.get("message"),"pattern_count":patterns.get("pattern_count",0),
                   "patterns":patterns.get("patterns",[])[:4],"weekday_profiles":behavior.get("weekday_profiles",[])[:7],
                   "insight":insight,"summary":f"Learning from {behavior.get('history_days',0)} measured day(s) with {patterns.get('pattern_count',0)} recurring pattern(s).",
                   "recorder_safe":True,"safety":"Recommendation only. No autonomous control."}
        try:self.event_bus.publish("LearningIntelligenceV2Updated","LearningIntelligenceV2",{"history_days":self.last["history_days"],"confidence":confidence["score"],"patterns":self.last["pattern_count"]})
        except Exception:pass
        return self.last

    def summary(self)->dict[str,Any]: return self.last
