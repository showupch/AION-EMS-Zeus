# AION EMS Zeus v16.0.500 — Dedicated Array Forecast Evidence (TEST)

- Adds `sensor.aion_ems_zeus_array_forecast_evidence`, a compact read-only diagnostic entity.
- Reports immutable array forecast capture status and comparison counts without relying on the large V2.1 Shadow payload.
- Shows whether the target-day snapshot predates array capture; never backfills or rewrites snapshots.
- Does not alter V2 production forecasting, V2.1 candidates, or controls.
- Runtime testing on Home Assistant is required before release.
