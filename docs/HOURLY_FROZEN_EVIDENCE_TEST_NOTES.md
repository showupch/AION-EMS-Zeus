v16.0.419 TEST — Frozen hourly forecast capture (stage 1)

- Persists the first complete tomorrow hourly audit (23/24/25 UTC buckets) in Home Assistant Store under the existing prediction accuracy storage key.
- Each record contains immutable original and daylight-guarded W values, target date and UTC capture timestamp. Existing target snapshots are never overwritten by refreshed weather.
- Capture occurs on the existing five-minute evidence timer and prediction-accuracy refresh, without triggering a new forecast.
- Exposes hourly_frozen_evidence in prediction accuracy summary, including capture status and count.
- Does NOT yet pair recorder hourly measured PV data; MAE remains unavailable. Daily actuals are not substituted for hourly evidence.
- No production V2 forecast, V2.1 training, or control changes.
