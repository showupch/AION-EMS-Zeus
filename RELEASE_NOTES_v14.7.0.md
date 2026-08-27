# AION EMS Zeus v14.7.0

## Stable release

AION EMS Zeus 14.7.0 promotes the validated 14.7 development line to stable.

### Highlights

- Supervised real my-PV ELWA execution with explicit ownership, readiness, arm and master-execution safety gates.
- Closed-loop ELWA solar modulation using live grid feedback and measured ELWA power, with a validated 400 W positive export reserve.
- 500–2000 W ELWA operating range, 800 W solar start threshold, 30 s active non-zero keepalive, one-shot STOP(0), and no idle zero traffic.
- Boiler lockout hysteresis, independent element-temperature taper and hard stop, plus separate 50–55 °C Grid Backup logic with Solar priority.
- Cleaner operational Smart Control & Safety UI with diagnostics collapsed away from the normal view.
- Native Zeus update status: up to date, update available, development build, or unable to check; no automatic installation.
- DHW Statistics fallback to a mapped Heat Pump DHW energy meter when no standalone water-heater device is registered.
- Heat Pump evidence sanity guards and profile-aware state normalization foundation.
- Viessmann Vitocal mapping improvements based on tester evidence: Kompressor Status, Betriebsmodus and Systemzustand semantics.
- All prior Command Center, Home Energy Timeline, Heat Pump Intelligence, Recorder-backed COP/cycle evidence and registered-device behavior are retained.

### Upgrade / safety notes

- Existing device mappings and saved ELWA settings are preserved.
- Real ELWA execution remains per-device supervised; safety/readiness gates continue to be evaluated continuously.
- The my-PV ELWA profile defaults for new setups are 400 W export reserve and 30 s active keepalive.
- No automatic update installation is performed by Zeus.

This stable package is built from the user-validated alpha.39.10 code line without additional control-algorithm changes.
