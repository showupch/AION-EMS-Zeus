AION EMS Zeus Forecast V2.1 — Phase 2 development package
Baseline: 16.0.411 (version intentionally unchanged)

Added read-only state adapter:
  custom_components/aion_ems_zeus/knowledge_v2_pkg/forecast_v21_site_reader.py
Added standalone tests:
  custom_components/aion_ems_zeus/tests/test_forecast_v21_site_reader.py

The adapter reads ONLY explicitly supplied Home Assistant entity mappings.
It validates units, nonnegative numeric readings, timestamp freshness,
and leaves unknown curtailment unqualified. No guesses about DC semantics.

NOT YET WIRED INTO LIVE HA ENTITY REGISTRATION OR HISTORY COLLECTION.
NOT AN INSTALLABLE RELEASE. DO NOT INSTALL OR PUBLISH THIS DEVELOPMENT ZIP.
No changes to Forecast V2, controls, or release version.

Standalone test functions: 11 passed. Full Home Assistant runtime not tested.
