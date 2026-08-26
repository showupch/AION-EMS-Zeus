# AION EMS Zeus v14.6.0 — Home Energy Timeline Foundation

Development starts from public stable v14.5.0.

- Adds a compact Home Energy Timeline to the free lower-right area of the Command Center flow canvas.
- Uses Zeus's existing persistent Observation & Knowledge store, so timeline events survive browser refreshes and Home Assistant restarts.
- Shows the four most recent meaningful events from the current local day.
- Reuses Solar, Grid, Battery and large-load transitions and adds registered-device start/stop events for Heat Pumps, Water Heaters, EV chargers and other registered consuming devices.
- Read-only and recommendation-only.
- Existing Solar/Grid/House/Battery/Loads geometry, Weather, Heat Pump and Water Heater kiosk panels, Energy Flow accounting and device control behavior are unchanged.
