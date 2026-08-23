"""AION EMS Zeus 7 core composition root."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .period_authority import configure_data_epoch
from .const import VERSION, DATA_LAKE_AUTO_CAPTURE_MINUTES
from .event_bus import AionEventBus
from .engines import (
    AnalyticsEngine,
    DashboardAPI,
    EnergyEngine,
    ForecastEngine,
    FinanceEngine,
    IntelligenceEngine,
    NotificationEngine,
    RegistryEngine,
    SchedulerEngine,
    SettingsAPI,
    HyperAnalyticsEngine,
    ZeusBrainCore,
    ObservationKnowledgeEngine,
    ReasoningExplainEngine,
    UpdateEngine,
)
from .discovery import DiscoveryEngine
from .pipeline import (
    IntegrationHub,
    DataBus,
    DataLake,
    SimpleSummaryEngine,
    KnowledgeEngine,
    BriefingCenter,
    QuestionLibrary,
    DiagnosticsEngine,
)
from .analytics import OptimizerEngine, DeviceAnalyticsEngine, DailyBriefingEngine
from .engines.weather_engine import WeatherEngine
from .engines.weather_history import WeatherHistoryEngine
from .engines.intelligence_core import LearningEngineV2, HomeEfficiencyEngine, PredictiveBatteryOptimizer, AIEnergyAdvisor, ConversationalZeusAssistant
from .data_quality import DataQualityEngine
from .migration import MigrationEngine
from .device_import import DeviceImportManager
from .device_import_wizard import DeviceImportWizard
from .ha_energy_import import HomeAssistantEnergyImporter
from .capability import CapabilityReport
from .qa_diagnostics import QADiagnosticsCenter
from .intelligence_quality_gate import IntelligenceQualityGate
from .runtime_resilience import RuntimeResilienceEngine
from .release_readiness import ReleaseReadinessEngine
from .data_consistency import DataConsistencyEngine
from .topology import MultiInverterTopologyEngine
from .planning_engine import PlanningEngine
from .optimization_engine import OptimizationIntelligenceEngine
from .insight_engine import InsightIntelligenceEngine
from .root_cause_engine import RootCauseIntelligenceEngine
from .correlation_confidence_engine import CorrelationConfidenceEngine
from .recommendation_priority_engine import RecommendationPriorityEngine
from .system_story_engine import SystemStoryEngine
from .energy_snapshot import EnergySnapshotService
from .device_energy_attribution import DeviceEnergyAttributionEngine
from .knowledge_v2 import (
    KnowledgeEngineV2,
    LearningIntelligenceV2,
    KnowledgeTimelineEngine,
    ExecutiveBriefingEngine,
    AdvisorV2,
    DecisionEngine,
    IntelligenceMemoryEngine,
    ScenarioSimulator,
    PredictionAccuracyEngine,
    HomeProfileEngine,
    AnomalyIntelligenceEngine,
    OpportunityLearningEngine,
    AdaptiveAdvisorEngine,
    IntelligenceFusionEngine,
)


class AionCore:
    """Compose and coordinate the stable Zeus engine API."""

    def __init__(self, hass, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.version = VERSION
        self.event_bus = AionEventBus(hass)

        # Core engines.
        self.registry = RegistryEngine(hass, self.event_bus)
        self.discovery = DiscoveryEngine(hass, self.event_bus)
        self.energy_engine = EnergyEngine(hass, self.event_bus, self.registry)

        # Backward-compatible aliases used by existing services and sensors.
        self.energy_mapping = self.energy_engine.mapping
        self.energy_flow = self.energy_engine.flow

        # Data and integration infrastructure.
        self.integration_hub = IntegrationHub(hass, self.event_bus, self.registry, self.discovery)
        self.energy_topology = MultiInverterTopologyEngine(hass, self.event_bus, self.registry, self.energy_flow)
        self.data_bus = DataBus(self.event_bus, self.energy_flow, self.registry)
        self.data_lake = DataLake(hass, self.event_bus, self.data_bus)
        self.diagnostics = DiagnosticsEngine(self.event_bus, self.registry)
        self.data_quality = DataQualityEngine(hass, self.event_bus, self.energy_flow, self.registry)

        # Import and migration tools.
        self.migration = MigrationEngine(hass, self.event_bus, self.registry)
        self.device_import_manager = DeviceImportManager()
        self.device_import_wizard = DeviceImportWizard(
            hass, self.event_bus, self.registry, self.discovery, self.energy_mapping
        )
        self.ha_energy_import = HomeAssistantEnergyImporter(
            hass, self.event_bus, self.registry, self.energy_mapping
        )

        # Analytics and decision engines.
        self.analytics = AnalyticsEngine(self.hass, self.event_bus, self.data_lake, self.registry)
        self.weather = WeatherEngine(self.hass, self.event_bus, self.registry)
        self.weather_history = WeatherHistoryEngine(self.hass, self.event_bus, self)
        self.history = self.analytics  # compatibility alias
        self.forecast = ForecastEngine(self.event_bus, self.data_lake, self.energy_flow, self.weather)
        self.optimizer = OptimizerEngine(
            self.hass, self.event_bus, self.registry, self.energy_flow, self.forecast
        )
        # Device Analytics is the canonical historical profile producer for the
        # Scheduler evidence bridge. It is constructed first; Scheduler consumes
        # its read-only summary and does not reproduce historical aggregation.
        self.device_analytics = DeviceAnalyticsEngine(self.hass, self.event_bus, self.data_lake, self.registry)
        self.scheduler = SchedulerEngine(
            self.event_bus, self.registry, self.forecast, self.optimizer, self.device_analytics
        )
        self.device_energy_attribution = DeviceEnergyAttributionEngine(self.hass, self.event_bus, self.registry, self.device_analytics)
        self.finance = FinanceEngine(self.event_bus, self.registry, self.analytics, self.device_analytics, self.data_quality)
        self.daily_briefing = DailyBriefingEngine(
            self.event_bus, self.analytics, self.device_analytics, self.optimizer, self.forecast
        )
        self.learning = LearningEngineV2(self.event_bus, self.data_lake)
        self.hyper_analytics = HyperAnalyticsEngine(self.event_bus, self.data_lake, self.analytics, self.finance, self.forecast, self.learning)
        self.brain = ZeusBrainCore(self.event_bus, self.energy_flow, self.hyper_analytics, self.forecast, self.finance, self.learning)
        self.observation_knowledge = ObservationKnowledgeEngine(self.hass, self.event_bus, self.energy_flow, self.data_lake, self.hyper_analytics, self.weather, self.registry)
        self.reasoning_explain = ReasoningExplainEngine(self.event_bus, self.energy_flow, self.observation_knowledge, self.hyper_analytics, self.forecast, self.finance)
        self.home_efficiency = HomeEfficiencyEngine(
            self.event_bus, self.analytics, self.energy_flow, self.data_quality
        )
        self.predictive_battery = PredictiveBatteryOptimizer(
            self.event_bus, self.forecast, self.energy_flow, self.analytics, self.learning,
            self.registry, self.scheduler
        )
        self.ai_advisor = AIEnergyAdvisor(
            self.event_bus, self.energy_flow, self.forecast, self.learning,
            self.home_efficiency, self.predictive_battery, self.optimizer
        )
        self.conversational_assistant = ConversationalZeusAssistant(
            self.event_bus, self.energy_flow, self.forecast, self.scheduler,
            self.predictive_battery, self.learning, self.analytics, self.finance,
            self.registry, self.ai_advisor, self.weather_history, None, self.device_analytics
        )

        # Knowledge, intelligence and presentation engines.
        self.knowledge = KnowledgeEngine(self.event_bus, self.data_lake)
        self.learning_intelligence_v2 = LearningIntelligenceV2(self.event_bus, self)
        self.knowledge_v2 = KnowledgeEngineV2(self.event_bus, self)
        self.knowledge_timeline = KnowledgeTimelineEngine(self.event_bus, self)
        self.intelligence_memory = IntelligenceMemoryEngine(self.hass, self.event_bus, self)
        self.executive_briefing = ExecutiveBriefingEngine(self.event_bus, self)
        self.opportunity_learning = OpportunityLearningEngine(self.hass, self.event_bus, self)
        self.adaptive_advisor = AdaptiveAdvisorEngine(self.event_bus, self)
        self.decision_engine = DecisionEngine(self.event_bus, self)
        self.scenario_simulator = ScenarioSimulator(self.event_bus, self)
        self.prediction_accuracy = PredictionAccuracyEngine(self.event_bus, self)
        self.home_profile = HomeProfileEngine(self.event_bus, self)
        self.anomaly_intelligence = AnomalyIntelligenceEngine(self.event_bus, self)
        self.intelligence_fusion = IntelligenceFusionEngine(self.event_bus, self)
        self.runtime_resilience = RuntimeResilienceEngine(self.event_bus)
        self.data_consistency = DataConsistencyEngine(self.event_bus, self)
        self.conversational_assistant.data_consistency = self.data_consistency
        self.intelligence_quality_gate = IntelligenceQualityGate(self.event_bus, self)
        self.release_readiness = ReleaseReadinessEngine(self.event_bus, self)
        self.advisor_v2 = AdvisorV2(self.event_bus, self)
        self.briefing = BriefingCenter(
            self.event_bus, self.registry, self.diagnostics, self.knowledge
        )
        self.intelligence = IntelligenceEngine(
            self.optimizer, self.knowledge, self.briefing, self.data_quality,
            self.forecast, self.energy_flow, self.registry
        )
        self.question_library = QuestionLibrary(
            self.event_bus,
            self.registry,
            self.diagnostics,
            self.data_lake,
            self.knowledge,
            self.briefing,
        )
        self.capability = CapabilityReport(hass, self.event_bus, self)
        self.notifications = NotificationEngine(
            self.hass, self.event_bus, self.energy_flow, self.intelligence, self.diagnostics, self.registry
        )
        self.settings_api = SettingsAPI(self.registry, self.energy_mapping)
        self.dashboard_api = DashboardAPI(self)
        self.qa_diagnostics = QADiagnosticsCenter(self.hass, self.event_bus, self.registry, self)
        self.planning_engine = PlanningEngine(self.hass, self.event_bus, self)
        # Enable forecast adaptation only after PlanningEngine and
        # PredictionAccuracyEngine exist, avoiding constructor cycles.
        self.forecast.core = self
        self.optimization_intelligence = OptimizationIntelligenceEngine(self.event_bus, self)
        self.energy_snapshot = EnergySnapshotService(self)
        self.insight_intelligence = InsightIntelligenceEngine(self.event_bus, self)
        self.root_cause_intelligence = RootCauseIntelligenceEngine(self.event_bus, self)
        self.correlation_confidence = CorrelationConfidenceEngine(self.event_bus, self)
        self.recommendation_priority = RecommendationPriorityEngine(self.event_bus, self)
        self.system_story = SystemStoryEngine(self.event_bus, self)

        # Event-driven synchronization is started last, after all dependencies exist.
        self.update_engine = UpdateEngine(
            hass,
            self.event_bus,
            self.tracked_entity_ids,
            self.refresh_live_pipeline,
        )
        self._unsub_auto_capture = None
        self._unsub_planning_capture = None
        self._startup_mapping_restore_unsubs: list = []
        self._startup_recovery_unsubs: list = []
        self._last_decision_refresh: datetime | None = None
        self._decision_refresh_interval = timedelta(minutes=15)
        self._last_quality_refresh: datetime | None = None
        self._last_topology_refresh: datetime | None = None
        self._quality_refresh_interval = timedelta(minutes=2)
        self._topology_refresh_interval = timedelta(seconds=30)
        self.performance: dict[str, object] = {
            "mode": "low_cpu_event_driven",
            "live_refreshes": 0,
            "decision_refreshes": 0,
            "last_live_duration_ms": None,
            "last_decision_duration_ms": None,
        }

    async def async_setup(self) -> None:
        await self.registry.async_load()
        configure_data_epoch((self.registry.data.get("home_settings") or {}).get("data_epoch"))
        await self.data_lake.async_load()
        await self.weather_history.async_load()
        await self.device_analytics.async_refresh_recorder_energy()
        self.device_analytics.refresh()
        await self.device_energy_attribution.async_refresh()
        await self.analytics.async_refresh_ha_energy_battery()
        await self.observation_knowledge.async_load()
        await self.intelligence_memory.async_load()
        await self.decision_engine.async_load()
        await self.opportunity_learning.async_load()
        await self.planning_engine.async_load()
        await self.integration_hub.async_discover_ha_mounts()
        # Modern HA weather forecasts are action responses, not entity attributes.
        # Fetch them before the first forecast/decision refresh so Zeus starts with
        # tomorrow/day-N weather evidence already available.
        self.weather.refresh()
        if hasattr(self.weather, "async_refresh_forecast"):
            await self.weather.async_refresh_forecast()
        self.refresh_pipeline()
        await self.weather_history.async_capture_today()
        # weather_history refreshes the provider forecast as part of capture;
        # refresh forecast-dependent engines once more so the newest rows are live.
        self._refresh_decision_and_api_engines()
        await self.update_engine.async_start()
        self._schedule_startup_mapping_restore()
        self._schedule_startup_engine_recovery()
        self.start_auto_capture()
        async def _capture_plan(_now=None):
            await self.planning_engine.async_capture_upcoming()
        self._unsub_planning_capture = async_track_time_interval(self.hass, _capture_plan, timedelta(minutes=30))
        self.event_bus.publish(
            "CoreStarted",
            "AionCore",
            {
                "version": self.version,
                "architecture": "zeus-12-5-executive-intelligence",
                "engines": self.engine_names(),
            },
        )

    async def async_unload(self) -> None:
        self.stop_auto_capture()
        if self._unsub_planning_capture:
            self._unsub_planning_capture()
            self._unsub_planning_capture = None
        self._cancel_startup_mapping_restore()
        self._cancel_startup_engine_recovery()
        await self.update_engine.async_stop()

    def _schedule_startup_mapping_restore(self) -> None:
        """Re-activate persisted mappings after source integrations finish startup.

        Home Assistant can restore the Zeus registry before every mapped source
        entity has published its first usable state. A finite set of retries
        prevents mappings (especially battery SOC) from remaining visually
        configured but inactive until the user presses Save again.
        """
        self._cancel_startup_mapping_restore()

        async def _restore(_now=None) -> None:
            self.update_engine.refresh_tracked_entities()
            self.refresh_live_pipeline(force_decisions=True)
            mappings = self.registry.data.get("entity_mappings", {})
            soc_entity = mappings.get("battery_soc")
            soc_state = self.hass.states.get(soc_entity) if soc_entity else None
            self.event_bus.publish(
                "EnergyMappingsRestored",
                "AionCore",
                {
                    "mapped_count": len(mappings),
                    "battery_soc_entity": soc_entity,
                    "battery_soc_available": bool(
                        soc_state
                        and str(soc_state.state).lower()
                        not in {"unknown", "unavailable", "none", ""}
                    ),
                },
            )

        # Finite, low-cost retries only; no continuous loop is introduced.
        for delay in (3, 15, 45):
            unsub = async_call_later(self.hass, delay, _restore)
            self._startup_mapping_restore_unsubs.append(unsub)

    def _cancel_startup_mapping_restore(self) -> None:
        for unsub in self._startup_mapping_restore_unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._startup_mapping_restore_unsubs.clear()


    def _schedule_startup_engine_recovery(self) -> None:
        """Retry delayed intelligence engines a finite number of times after startup."""
        self._cancel_startup_engine_recovery()

        async def _recover(_now=None) -> None:
            self.runtime_resilience.mark_recovery_run()
            self.update_engine.refresh_tracked_entities()
            self.refresh_live_pipeline(force_decisions=True)

        # Finite retries only. These cover restored and late-loading entities without polling.
        for delay in (8, 30, 90):
            self._startup_recovery_unsubs.append(async_call_later(self.hass, delay, _recover))

    def _cancel_startup_engine_recovery(self) -> None:
        for unsub in self._startup_recovery_unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._startup_recovery_unsubs.clear()

    def _guarded_refresh(self, name: str, engine: object) -> bool:
        callback = getattr(engine, "refresh", None)
        if not callable(callback):
            return self.runtime_resilience.guarded_refresh(
                name, lambda: (_ for _ in ()).throw(AttributeError("refresh unavailable"))
            )
        return self.runtime_resilience.guarded_refresh(name, callback)

    def engine_names(self) -> list[str]:
        return [
            "Update Engine",
            "Energy Engine",
            "Registry Engine",
            "Data Quality Engine",
            "Analytics Engine",
            "Device Analytics Engine",
            "Weather Engine",
            "Weather History Engine",
            "Daily Briefing Engine",
            "Finance Engine",
            "Optimizer Engine",
            "Intelligence Engine",
            "Forecast Engine",
            "Learning Engine 2.0",
            "Home Efficiency Engine",
            "Predictive Battery Optimizer",
            "AI Energy Advisor",
            "Conversational Zeus Assistant",
            "Observation & Knowledge Engine",
            "Knowledge Engine 2.0",
            "Knowledge Timeline Engine",
            "Intelligence Memory Engine",
            "Executive Briefing Engine",
            "Decision Engine",
            "Advisor 2.0",
            "Scheduler Engine",
            "Notification Engine",
            "Integration Hub & Plugin Marketplace",
            "Multi-Inverter & Energy Topology Engine",
            "Dashboard API",
            "Settings API",
            "QA & Diagnostics Center",
            "Optimization Intelligence Engine",
            "Root Cause Intelligence Engine",
            "Correlation & Confidence Engine",
        ]

    def tracked_entity_ids(self) -> set[str]:
        """Return all mapped and registered source entities watched in real time."""
        entities: set[str] = set()
        mappings = self.registry.data.get("entity_mappings", {})
        for value in mappings.values():
            if isinstance(value, str) and "." in value:
                entities.add(value)
            elif isinstance(value, dict):
                entity_id = value.get("entity_id")
                if entity_id:
                    entities.add(entity_id)
        for device in self.registry.data.get("devices", []):
            if not device.get("enabled", True):
                continue
            for key in ("power_entity", "energy_entity", "state_entity", "availability_entity"):
                entity_id = device.get(key)
                if entity_id:
                    entities.add(entity_id)
        return entities

    def _refresh_decision_and_api_engines(self) -> None:
        """Refresh intelligence engines independently so one failure cannot block Zeus."""
        started = datetime.now(timezone.utc)
        ordered_engines = (
            ("data_quality", self.data_quality),
            ("energy_topology", self.energy_topology),
            ("analytics", self.analytics),
            ("weather_history", self.weather_history),
            ("weather", self.weather),
            ("forecast", self.forecast),
            ("optimizer", self.optimizer),
            # Refresh canonical historical device evidence before Scheduler so
            # qualification decisions use the latest Device Analytics summary.
            ("device_analytics", self.device_analytics),
            ("scheduler", self.scheduler),
            ("finance", self.finance),
            ("daily_briefing", self.daily_briefing),
            ("learning", self.learning),
            ("hyper_analytics", self.hyper_analytics),
            ("brain", self.brain),
            ("observation_knowledge", self.observation_knowledge),
            ("reasoning_explain", self.reasoning_explain),
            ("home_efficiency", self.home_efficiency),
            ("predictive_battery", self.predictive_battery),
            ("ai_advisor", self.ai_advisor),
            ("optimization_intelligence", self.optimization_intelligence),
            ("insight_intelligence", self.insight_intelligence),
            ("knowledge", self.knowledge),
            ("learning_intelligence_v2", self.learning_intelligence_v2),
            ("knowledge_v2", self.knowledge_v2),
            ("intelligence_memory", self.intelligence_memory),
            ("knowledge_timeline", self.knowledge_timeline),
            ("decision_engine", self.decision_engine),
            ("opportunity_learning", self.opportunity_learning),
            ("adaptive_advisor", self.adaptive_advisor),
            ("scenario_simulator", self.scenario_simulator),
            ("prediction_accuracy", self.prediction_accuracy),
            ("home_profile", self.home_profile),
            ("anomaly_intelligence", self.anomaly_intelligence),
            ("intelligence_fusion", self.intelligence_fusion),
            ("executive_briefing", self.executive_briefing),
            ("advisor_v2", self.advisor_v2),
            ("briefing", self.briefing),
            ("intelligence", self.intelligence),
            ("question_library", self.question_library),
            ("capability", self.capability),
            ("notifications", self.notifications),
            ("settings_api", self.settings_api),
            ("dashboard_api", self.dashboard_api),
        )
        for name, engine in ordered_engines:
            self._guarded_refresh(name, engine)

        # Data consistency runs after all producers and before the quality gate.
        self._guarded_refresh("data_consistency", self.data_consistency)
        self._guarded_refresh("conversational_assistant", self.conversational_assistant)
        # Quality Gate runs after the intelligence stack and includes resilience health.
        self._guarded_refresh("intelligence_quality_gate", self.intelligence_quality_gate)
        # Release Readiness runs last so it sees the latest quality and resilience evidence.
        self._guarded_refresh("release_readiness", self.release_readiness)
        completed = datetime.now(timezone.utc)
        self.performance["decision_refreshes"] = int(self.performance.get("decision_refreshes", 0)) + 1
        self.performance["last_decision_duration_ms"] = round((completed - started).total_seconds() * 1000, 1)
        self.performance["last_decision_refresh"] = completed.isoformat()
        # QA is user-triggered to avoid filesystem and registry checks every minute.

    def refresh_live_pipeline(self, *, force_decisions: bool = False) -> None:
        """Refresh only live energy immediately; defer heavier engines."""
        started = datetime.now(timezone.utc)
        self.energy_engine.refresh()
        self.data_bus.refresh()
        self.data_lake.refresh_mapped_energy_today()
        self.data_lake.refresh_summary()

        now = datetime.now(timezone.utc)
        if (
            self._last_topology_refresh is None
            or now - self._last_topology_refresh >= self._topology_refresh_interval
        ):
            self.energy_topology.refresh()
            self._last_topology_refresh = now

        if (
            self._last_quality_refresh is None
            or now - self._last_quality_refresh >= self._quality_refresh_interval
        ):
            self.diagnostics.refresh()
            self.data_quality.refresh()
            self._last_quality_refresh = now

        decision_due = (
            force_decisions
            or self._last_decision_refresh is None
            or now - self._last_decision_refresh >= self._decision_refresh_interval
        )
        if decision_due:
            self._refresh_decision_and_api_engines()
            self._last_decision_refresh = now

        completed = datetime.now(timezone.utc)
        self.performance["live_refreshes"] = int(self.performance.get("live_refreshes", 0)) + 1
        self.performance["last_live_duration_ms"] = round((completed - started).total_seconds() * 1000, 1)
        self.performance["last_live_refresh"] = completed.isoformat()

    def refresh_pipeline(self) -> None:
        """Refresh the complete Zeus pipeline, including discovery and summaries."""
        self.discovery.refresh()
        self.energy_engine.refresh()
        self.integration_hub.refresh()
        self.data_bus.refresh()
        self.data_lake.refresh_mapped_energy_today()
        self.diagnostics.refresh()
        self.data_lake.refresh_summary()
        self._refresh_decision_and_api_engines()

    async def async_capture_pipeline_snapshot(self) -> None:
        # Refresh live measurements first, then retrieve modern HA future weather
        # before decision engines calculate the forecast.
        self.refresh_live_pipeline(force_decisions=False)
        self.weather.refresh()
        if hasattr(self.weather, "async_refresh_forecast"):
            await self.weather.async_refresh_forecast()
        self._refresh_decision_and_api_engines()
        await self.data_lake.async_capture_snapshot()
        await self.intelligence_memory.async_capture_today()
        await self.weather_history.async_capture_today()
        await self.device_analytics.async_refresh_recorder_energy()
        self.device_analytics.refresh()
        await self.device_energy_attribution.async_refresh()
        await self.analytics.async_refresh_ha_energy_battery()
        self.data_lake.refresh_summary()

    async def _async_auto_capture(self, now=None) -> None:
        try:
            await self.async_capture_pipeline_snapshot()
        except Exception as err:  # Home Assistant must keep running on capture errors.
            self.event_bus.publish(
                "AutoCaptureFailed", "AionCore", {"error": str(err)}
            )

    def start_auto_capture(self) -> None:
        if self._unsub_auto_capture is None:
            self._unsub_auto_capture = async_track_time_interval(
                self.hass,
                self._async_auto_capture,
                timedelta(minutes=DATA_LAKE_AUTO_CAPTURE_MINUTES),
            )
            self.event_bus.publish(
                "AutoCaptureStarted",
                "AionCore",
                {"minutes": DATA_LAKE_AUTO_CAPTURE_MINUTES},
            )

    def stop_auto_capture(self) -> None:
        if self._unsub_auto_capture is not None:
            self._unsub_auto_capture()
            self._unsub_auto_capture = None
        self._unsub_planning_capture = None

    def summary(self) -> dict:
        """Return the complete backend state while preserving legacy keys."""
        return {
            "version": self.version,
            "architecture": "zeus-12-5-executive-intelligence",
            "engines": self.engine_names(),
            "registry": self.registry.summary(),
            "discovery": self.discovery.summary(),
            "energy_engine": self.energy_engine.summary(),
            "energy_mapping": self.energy_mapping.summary(),
            "energy_flow": self.energy_flow.summary(),
            "update_engine": self.update_engine.summary(),
            "performance": dict(self.performance),
            "integration_hub": self.integration_hub.summary(),
            "energy_topology": self.energy_topology.summary(),
            "data_bus": self.data_bus.summary(),
            "data_lake": self.data_lake.summary(),
            "data_quality": self.data_quality.summary(),
            "analytics": self.analytics.summary(),
            "weather_history": self.weather_history.summary(),
            "history": self.history.summary(),
            "learning": self.learning.summary(),
            "hyper_analytics": self.hyper_analytics.summary(),
            "brain": self.brain.summary(),
            "observation_knowledge": self.observation_knowledge.summary(),
            "reasoning_explain": self.reasoning_explain.summary(),
            "conversational_assistant": self.conversational_assistant.summary(),
            "home_efficiency": self.home_efficiency.summary(),
            "forecast": self.forecast.summary(),
            "optimizer": self.optimizer.summary(),
            "device_analytics": self.device_analytics.summary(),
            "daily_briefing": self.daily_briefing.summary(),
            "intelligence": self.intelligence.summary(),
            "scheduler": self.scheduler.summary(),
            "notifications": self.notifications.summary(),
            "dashboard_api": self.dashboard_api.summary(),
            "settings_api": self.settings_api.summary(),
            "diagnostics": self.diagnostics.summary(),
            "knowledge": self.knowledge.summary(),
            "learning_intelligence_v2": self.learning_intelligence_v2.summary(),
            "knowledge_v2": self.knowledge_v2.summary(),
            "knowledge_timeline": self.knowledge_timeline.summary(),
            "intelligence_memory": self.intelligence_memory.summary(),
            "executive_briefing": self.executive_briefing.summary(),
            "opportunity_learning": self.opportunity_learning.summary(),
            "adaptive_advisor": self.adaptive_advisor.summary(),
            "decision_engine": self.decision_engine.summary(),
            "scenario_simulator": self.scenario_simulator.summary(),
            "prediction_accuracy": self.prediction_accuracy.summary(),
            "home_profile": self.home_profile.summary(),
            "anomaly_intelligence": self.anomaly_intelligence.summary(),
            "intelligence_fusion": self.intelligence_fusion.summary(),
            "runtime_resilience": self.runtime_resilience.summary(),
            "data_consistency": self.data_consistency.summary(),
            "intelligence_quality_gate": self.intelligence_quality_gate.summary(),
            "release_readiness": self.release_readiness.summary(),
            "advisor_v2": self.advisor_v2.summary(),
            "briefing": self.briefing.summary(),
            "question_library": self.question_library.summary(),
            "device_import_wizard": self.device_import_wizard.summary(),
            "capability": self.capability.summary(),
            "qa_diagnostics": self.qa_diagnostics.summary(),
        }
