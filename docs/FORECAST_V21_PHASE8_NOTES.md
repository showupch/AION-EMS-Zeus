# V2.1 Phase 8 — development only

Adds a pure read-only bridge from PredictionAccuracy frozen daily samples to the independently developed candidate evaluator.

- Explicit HA-local timezone and DST-aware pre-day cutoff.
- No invented historical weather or forecast data.
- Duplicate and future-day rejection.
- Optional site evidence may veto but never force a candidate.
- No runtime wiring, persistence, entity changes, or production forecast overrides in this phase.

**Do not install as a production release.** Keep v16.0.411 installed.
