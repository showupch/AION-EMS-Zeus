# AION EMS Zeus v16.0.525 — Forecast V2 Diagnostics

## Changes since v16.0.505

- Added read-only hourly Forecast V2 diagnostics for investigation of persistent underprediction.
- Corrected diagnostic labeling to distinguish the Legacy historical baseline from the V2 reconciled baseline.
- Exposed calculation stages, weather reductions, guard effects, daily totals and daylight/time-alignment checks.
- Retained Forecast V2 as the production engine. Experimental V2.1, multi-array and radiation models remain shadow-only.

## How to collect evidence

In the Home Assistant browser developer console, run:

```javascript
document.querySelector("home-assistant").hass.callWS({
  type: "aion_ems_zeus/forecast_v2_hourly_diagnostics"
}).then(r => console.log(JSON.stringify(r, null, 2)));
```

Share the JSON output, Zeus version, date and local timezone. Remove private information before posting.

## Notes

This is a diagnostic release, not a forecast-accuracy correction. Do not interpret the diagnostic baselines as measured clear-sky production. Validation in a running Home Assistant installation is required before broad deployment.
