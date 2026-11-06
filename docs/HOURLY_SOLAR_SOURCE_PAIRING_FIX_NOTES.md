# v16.0.436 — Hourly solar Recorder source pairing (read-only)

- A configured solar Energy-dashboard source with **zero** hourly statistics can be excluded only if at least one other source has all target-day hours and their summed energy reconciles with Zeus's completed-day canonical solar actual (within max(0.1 kWh, 2%)).
- Any partially populated source blocks pairing; missing hours are never synthesized.
- No measured daily actual or a disagreement blocks pairing.
- Sources with rows but no valid hourly evidence block pairing.
- A blocked older frozen snapshot no longer prevents attempting later completed days.
- Each paired snapshot records the included/excluded statistic IDs and reconciliation reason.
- No Energy dashboard settings, snapshots, production forecasts, or controls are modified.

## Testing
Seven isolated pure-Python source-selection cases passed, Python compilation passed, and JavaScript syntax passed. Full Home Assistant Recorder integration testing remains necessary.
