# AION EMS Zeus v16.0.491 — Heat Pump Source Diagnostics

- Fix missing `electrical_power_source_audit` in the compact Heat Pump Intelligence sensor attributes.
- Expose primary, heating, DHW and cooling electrical source entities and their live values, along with the selected COP denominator and source policy.
- Diagnostic only: no changes to heat-pump accounting, energy flows, COP, forecasts, or device control.
- GitHub issue #5 remains open pending reporter evidence.

After updating, inspect Developer Tools → States → `sensor.aion_ems_zeus_heat_pump_intelligence` → `devices` → `electrical_power_source_audit`.
