# AION EMS Zeus v14.0.0-alpha.22.18.1.110

Confirmed development baseline.

## Highlights
- Heat Pump COP Performance Intelligence validated with a real active COP run.
- DHW Intelligence Phase 2 with Recorder-backed energy, temperature evidence, live timing guidance and DEA source attribution.
- EV & Flexible Load Intelligence Phase 1 with measured flexible-load power, Recorder energy, DEA Solar/Battery/Grid attribution, tariff value context and recommendation-only solar-surplus guidance.
- DEA backend fixes:
  - on-demand live refresh
  - Registry mapping authority
  - explicit end-use load classification
  - runtime diagnostics
- Kiosk branding corrected to `By V.T. Switzerland`.

## Validation
Confirmed in the live Home Assistant installation:
- Heat Pump COP performance: PASS
- DHW DEA attribution: PASS
- EV/Flexible DEA attribution: PASS
- Recommendation-only safety preserved
- No automatic device control

## HACS
This repository is prepared for installation/update through HACS as a custom Integration repository.

Repository:
`https://github.com/showupch/AION-EMS-Zeus`

Version:
`14.0.0-alpha.22.18.1.110`
