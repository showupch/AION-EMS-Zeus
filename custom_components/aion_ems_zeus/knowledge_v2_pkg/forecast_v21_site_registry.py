"""Read-only Phase 6 bridge: Zeus registry -> V2.1 site evidence diagnostics.

No automatic entity mapping, no registry mutation, no service calls, no
persistence, and no forecast/control changes. Admin-approved mapping settings
must be present in home_settings.v21_site_evidence before any sensor is read.
"""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from .forecast_v21_site_config import inspect_configured_site


def audit_registry_site(hass: Any, registry_data: Mapping[str, Any] | None) -> dict[str, Any]:
    data = registry_data if isinstance(registry_data, Mapping) else {}
    home = data.get("home_settings")
    home = home if isinstance(home, Mapping) else {}
    settings = home.get("v21_site_evidence")
    settings = settings if isinstance(settings, Mapping) else {}
    devices = data.get("devices")
    devices = devices if isinstance(devices, list) else []
    report = inspect_configured_site(hass, settings, devices)
    instant = report.get("instant_observation") or {}
    evidence = instant.get("evidence") or {}
    return {
        "status": "unconfigured" if not report["configured_keys"] else "read_only",
        "configured_keys": report["configured_keys"],
        "invalid_mapping_keys": report["invalid_mapping_keys"],
        "missing_keys": report["missing_keys"],
        "pv_dc_semantics": report["pv_dc_semantics"],
        "curtailment_known": report["curtailment_known"],
        "candidate_inverter_entities": report["candidate_inverter_entities"],
        "qualified_evidence_keys": sorted(k for k, v in evidence.items() if isinstance(v, dict) and v.get("qualified")),
        "rejected": instant.get("rejected", {}),
        "production_applied": False,
        "control_permission": False,
        "persistent": False,
        "note": "Diagnostic snapshot only. No sampling scheduled; mapping requires explicit administrator configuration.",
    }
