# AION EMS Zeus v14.0.0-alpha.22.18.1.110 — DEA Explicit Load Classifier Fix

Cumulative from .109.

## Proven narrowing
Runtime .109 reported:
- 6 registered flexible loads
- 6 with valid DEA-eligible power entities
- DEA returned 0 device rows

DEA creates rows before Recorder attribution, so Recorder history was not the cause.
The remaining pre-row gate was the shared `is_consuming_load()` classifier.

## Fix
Explicit Zeus end-use types are now authoritative consuming loads, including:
EV chargers, smart plugs, dishwasher, washing machine, dryer, refrigerator,
heat pump, DHW/water heater, computers, TV, blowers/fans and generic custom loads.

Generic HA metadata such as `power meter` in device class/category can no longer
misclassify an explicitly registered end-use appliance as a source device.

Explicit inverter/generation/meter types remain excluded.

## Diagnostics
DEA 1.13 now exposes registry counts:
- total registered devices
- devices with power_entity
- devices classified as consuming loads
- resulting DEA rows

No attribution is fabricated.

Preserved:
- .109 on-demand DEA refresh
- DHW DEA pass
- Heat Pump COP pass
- EV/Flexible planning logic
- By V.T. Switzerland
- no German branch
