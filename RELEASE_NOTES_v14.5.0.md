# AION EMS Zeus v14.5.0 — Stable Release

This stable release promotes the validated 14.5 development train following the public v14.4.2 release.

## Highlights

- Advanced manufacturer-independent Heat Pump circuit model.
- Heat Pump Intelligence with evidence-backed operating interpretation, confidence, coherence and self-relative COP performance.
- Evidence-backed Observed Activity classification for Standby, Heating and other supported thermal activity.
- Recorder-backed compressor cycle history, runtime profile and restart-pattern intelligence.
- Correct inactive/standby COP semantics and conservative handling when thermal-power evidence is unavailable.
- Registered-device economics and expanded device analytics.
- Locked Command Center kiosk enhancements with conditional Heat Pump and Water Heater panels.
- Canonical registered/unknown load accounting retained.
- Multi-inverter safety: canonical site Solar mappings remain authoritative and registered inverter devices are not added to site Solar.
- Canonical daily-energy mapping guard prevents a cumulative total mapped into a `*_today` slot from being exposed as lifetime energy for the current day.
- Finance guard prevents duplicate cumulative today/total mappings from overriding canonical daily or Recorder-derived battery energy.
- Code-hygiene and frontend/backend contract audit completed.

## Compatibility

- Existing Zeus registry, Energy Sources mappings, settings and stored intelligence are preserved.
- HACS/GitHub stable update path remains supported.
- Recommendation-only safety behavior is unchanged.
- The validated v14.5.22 Command Center kiosk geometry and behavior are preserved.
