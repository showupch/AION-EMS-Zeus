# AION EMS Zeus v14.6.10 — Heat Pump Mapping Guidance & Production Clarity

Built cumulatively from v14.6.9.

- Adds explicit help text to Heat Pump Power/Energy inputs: these are electrical input/consumption.
- Clarifies Thermal Power as instantaneous produced heat (W/kW).
- Clarifies Thermal Energy as combined accumulated produced heat (Wh/kWh/MWh); total_increasing kWh is supported.
- Explicitly tells users to leave combined Thermal Energy empty when only separate Heating/DHW thermal-energy sensors exist.
- Clarifies DHW Energy, Heating Energy and Cooling Energy as separate thermal-output evidence.
- Adds a concise Heat Pump mapping guide inside Advanced Heat Pump Inputs.
- Kiosk Production now says `No live thermal power` when no Thermal Power entity is mapped, instead of the ambiguous `Unavailable`.
- v14.6.9 persistence hardening is retained.
- No Energy Flow accounting, Timeline logic, Heat Pump intelligence calculations, finance or device-control changes.
