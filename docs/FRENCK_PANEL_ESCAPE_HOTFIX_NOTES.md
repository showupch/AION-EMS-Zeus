# AION EMS Zeus v16.0.432 — Panel HTML Escaping Security Fix

- Escapes the raw selected weather entity temperature before interpolating it into the Weather Sources panel HTML.
- Uses the integration's safe this.money() method for Mission Control financial values instead of directly interpolating finance.currency.
- No changes to forecast predictions, control behavior, entity mappings, or experimental forecast diagnostics.
- Versions synchronized: VERSION, manifest.json, const.py.
- Based on v16.0.431 full release code.
- Reported by @frenck during HACS/default PR #10581 review.
