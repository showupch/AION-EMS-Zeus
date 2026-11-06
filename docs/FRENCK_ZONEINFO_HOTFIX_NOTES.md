# v16.0.431 — Home Assistant event-loop timezone hotfix

Full HACS release based on the published v16.0.421 main-branch source. Experimental v16.0.422–v16.0.430 diagnostics were TEST-only and are not included in this compliance release.

- Pass `dt_util.get_default_time_zone()` (an existing `tzinfo`) to both V2.1 comparison evaluators, eliminating `ZoneInfo(timezone_name)` construction on the event loop.
- Reuse the configured timezone object to construct local midnight boundaries for frozen-hourly Recorder pairing, preserving DST behavior.
- No changes to forecasting math, cloud/solar evidence, history, automation, or smart-control logic.
- Thanks to Frenck for the event-loop I/O review.
