# AION EMS Zeus v14.6.12 — Stable Release

AION EMS Zeus v14.6.12 consolidates the latest real-world validation work across the Command Center, registered devices, Home Energy Timeline and Heat Pump support.

## Highlights

### Home Energy Timeline
- Adds an 8-row current-day energy event timeline to the Command Center.
- Records meaningful registered-device transitions such as EV charging and water-heater start/stop events.
- Records Heat Pump start/stop evidence.
- Supports repeated start → stop → start sessions within the same hour.
- Uses live registered-device refreshes so short sessions are not hidden behind the slower intelligence cadence.
- Preserves evidence-backed wording and does not invent a start transition after restart when it was not observed.

### Command Center
- Keeps the validated full-screen kiosk geometry.
- Improves small-text readability for 24-inch displays.
- Removes the duplicate DHW temperature row from the left Energy Sources panel.
- Heat Pump Consumption remains electrical input power.
- Heat Pump Production shows the mapped compressor/thermal output power in kW.
- Conditional Heat Pump and Water Heater blocks remain registry-driven.

### Heat Pump mapping and persistence
- Hardens Registered Device updates so omitted fields from older/cached frontends do not erase existing Advanced Heat Pump mappings.
- Normalizes older Heat Pump registry records so advanced fields remain explicit and export-safe.
- Clarifies that top-level Power/Energy are electrical consumption.
- Clarifies Thermal Power as current produced heat/output power.
- Clarifies Thermal Energy as combined accumulated produced heat.
- Clarifies separate Heating Energy, DHW Energy and Cooling Energy mappings.
- A combined Thermal Energy sensor may be left empty when only separate Heating/DHW thermal-energy meters are available.

## Validation
This release has been exercised against real Home Assistant operation including:
- registered EV charging start/stop/restart transitions;
- registered water-heater start/stop transitions;
- Heat Pump start/stop transitions;
- Heat Pump advanced mapping save/export persistence;
- compressor electrical input and output-power presentation;
- registered/unknown load accounting and the established Command Center flow layout.

## Safety
Zeus remains Recommendation Only by default. This release does not introduce automatic device control.

Existing registry data, mappings, settings and stored intelligence are preserved.
