# AION EMS Zeus Core 7 Architecture

`AionCore` is the composition root. Engines communicate through explicit dependencies and the internal event bus.

## Stable engine surface

- **Update Engine** — source-entity subscriptions, debounce and safety refresh.
- **Energy Engine** — mappings, unit normalization and live energy-flow calculations.
- **Registry Engine** — persistent devices, rooms, groups and mappings.
- **Analytics Engine** — compact historical summaries.
- **Intelligence Engine** — explainable recommendations, knowledge and briefings.
- **Forecast Engine** — history-based forecast baseline.
- **Scheduler Engine** — recommendation-only suggested schedules.
- **Notification Engine** — deduplicated notification candidates; delivery disabled in 7.0.
- **Dashboard API** — one stable read-only payload for the native Zeus application.
- **Settings API** — one validated configuration surface; frontend code does not access storage directly.

## Compatibility

The legacy attributes `energy_mapping`, `energy_flow`, `history`, `optimizer`, `forecast` and `scheduler` remain available so existing sensors and services continue to work.

## Safety boundary

Zeus 7.0 does not call device-control services. Optimizer, Scheduler and Notification outputs are previews or recommendations only.
