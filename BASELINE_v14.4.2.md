# AION EMS Zeus v14.4.2 — Stable Update

AION EMS Zeus 14.4.2 is the next stable release after v14.0.0.

## Highlights

### Forecast Intelligence v2
- Forward forecast validation at 1h / 3h / 6h / 12h / 24h.
- Stores tomorrow's forecast before the outcome occurs.
- Daily forecast outcome matching against canonical Home Assistant Recorder data.
- Separate daily forecast accuracy, bias, mean error and MAE for:
  - Solar
  - Home consumption
  - Grid import
  - Grid export

### Planning & Optimization
- Advisory strategy comparison:
  - Lowest Cost
  - Highest Self-Consumption
  - Lowest Grid Import
- Dependency-free constrained discrete optimization.
- Best schedule found within the enumerated feasible candidate windows.
- Explicitly does not claim a global continuous mathematical optimum.
- Recommendation Only remains enforced.

### Device Scheduling Intelligence
- Separates "can run" from "should run".
- Flexible classification alone no longer creates an actionable recommendation.
- Available solar and historical cadence alone never prove a device needs to run.
- Constrained optimization consumes only need-supported Scheduler rows.
- Compact Planning Intelligence learning state when no actionable schedule exists.

### Inverter Compatibility
- Adds Kostal / Piko / Plenticore recognition.
- Adds entity-backed inverter discovery for suitable Home Assistant entities,
  including generic Modbus-backed inverter setups.
- Home Assistant Device Registry remains the preferred inverter identity source.
- Helper-only entities cannot create an inverter identity by themselves.

## Preserved
- Approved Zeus v14 human-first UI.
- Recorder-backed measured data authority.
- Existing Forecast / Scheduler / Finance / Memory / Copilot behavior.
- No automatic device control.
- No Home Assistant service calls from the optimizer.
- No direct Modbus/Kostal transport implementation; Zeus reads Home Assistant entities only.

This release establishes v14.4.2 as the current stable Zeus baseline.
