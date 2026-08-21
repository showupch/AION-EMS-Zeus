"""AION EMS capability report."""

from __future__ import annotations

from .const import DOMAIN


class CapabilityReport:
    SERVICES = [
        "refresh_capability_report",
        "refresh_entity_discovery",
        "refresh_integration_hub",
        "refresh_data_bus",
        "capture_data_lake_snapshot",
        "import_discovery_candidate",
        "import_recommended_devices",
        "remove_auto_imported_devices",
        "backup_registry",
    ]

    ENGINES = ["registry", "discovery", "integration_hub", "data_bus", "data_lake", "knowledge", "briefing", "question_library", "diagnostics", "migration"]

    def __init__(self, hass, event_bus, core) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.core = core
        self.last = {"status": "Not generated"}

    def refresh(self):
        missing_services = [{"service": f"{DOMAIN}.{s}"} for s in self.SERVICES if not self.hass.services.has_service(DOMAIN, s)]
        missing_engines = [{"engine": e} for e in self.ENGINES if not hasattr(self.core, e)]
        self.last = {
            "status": "Ready" if not missing_services and not missing_engines else "Warning",
            "service_count": len(self.SERVICES),
            "missing_service_count": len(missing_services),
            "engine_count": len(self.ENGINES),
            "missing_engine_count": len(missing_engines),
            "missing_services": missing_services,
            "missing_engines": missing_engines,
            "next_steps": ["Import devices and let Data Lake collect snapshots."],
            "summary": f"{len(self.SERVICES)-len(missing_services)}/{len(self.SERVICES)} services, {len(self.ENGINES)-len(missing_engines)}/{len(self.ENGINES)} engines ready.",
            "safety": "Report only.",
        }
        return self.last

    def summary(self):
        return self.last
