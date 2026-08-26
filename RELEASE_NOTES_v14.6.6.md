# AION EMS Zeus v14.6.6 — Home Energy Timeline Live Device Event Fix

Built cumulatively from the locked v14.6.5 kiosk baseline.

- Refreshes the lightweight Observation/Timeline engine on every mapped or registered-device live refresh instead of waiting for the 15-minute decision cadence.
- Prevents short EV charging or registered-device sessions from being missed by the Timeline.
- On first baseline after install/restart, an already-active registered device is recorded as `active` rather than falsely claiming Zeus witnessed its start.
- EV chargers use `charging active/started/stopped` wording.
- The locked v14.6.5 Command Center frontend is byte-for-byte unchanged.
- No Energy Flow accounting, finance, Heat Pump intelligence, device analytics calculations or device-control changes.
