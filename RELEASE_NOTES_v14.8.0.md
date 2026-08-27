# AION EMS Zeus v14.8.0

## Stable supervised-control release

AION EMS Zeus v14.8.0 consolidates the validated supervised ELWA control work, operational UI cleanup, heat-pump evidence improvements and native update-status support into the next stable release line.

### my-PV ELWA supervised control

- Real supervised Modbus execution for my-PV AC ELWA 2.
- Dynamic solar-surplus power modulation instead of simple ON/OFF control.
- Closed-loop grid-export regulation using live grid feedback.
- Validated 400 W positive export reserve.
- 500–2000 W configurable ELWA power range.
- 800 W solar start threshold.
- 30-second active non-zero keepalive; idle 0 W produces no keepalive traffic.
- Immediate safe STOP(0) on stop/safety transitions.
- Boiler lockout hysteresis and independent element-temperature taper/hard stop.
- Separate 50–55 °C Grid Backup comfort band with Solar priority.
- Exclusive Zeus/Home Assistant ownership handover and rollback path.
- 18 execution-readiness gates, explicit arm request/confirmation, master enable and Emergency Stop.

### Supervised Execution editor

- Final two-row layout with four clearly separated controls.
- Row 1: Request execution arm / Confirm operator arm intent.
- Row 2: Enable real supervised ELWA execution / Emergency Stop.
- Emergency Stop is independently bound and visually separated from the execution master.
- Safe post-upgrade behavior is preserved: real execution defaults OFF after upgrade and requires deliberate re-enable.

### Smart Control & Safety UI

- Cleaner operational Control Status and Live Control Decision views.
- Compact readiness status with detailed gates available on demand.
- Control ownership / rollback panel and advanced command diagnostics moved out of the normal workflow.
- Correct supervised-execution safety wording for active control builds.

### Heat Pump / DHW intelligence

- DHW Energy History can fall back to the mapped Heat Pump DHW energy meter when no standalone water-heater device is registered.
- DHW history is hidden when no valid source exists instead of showing misleading zero values.
- Heat-pump circuit sanity guards prevent implausible temperature deltas from being treated as valid intelligence.
- Manufacturer/profile-aware state-normalization foundation.
- Viessmann Vitocal support improved using real Home Assistant evidence:
  - binary compressor status as authoritative compressor activity,
  - operating-mode normalization for relevant Vitocal modes,
  - System / Activity State support for values such as `standby` and `Warmwasser_aktiv`.

### Zeus native update status

- Native Zeus update-state panel with UP TO DATE, UPDATE AVAILABLE, DEVELOPMENT BUILD and UNABLE TO CHECK states.
- SemVer-aware stable/prerelease comparison.
- Informational only; Zeus never auto-installs an update.

### Compatibility and non-regression

- Existing Command Center, Home Energy Timeline, Heat Pump Intelligence v1/v2, Recorder-backed evidence, registered/unknown load accounting and previous device mappings are preserved.
- Existing supervised-control settings remain device-specific and opt-in.

## Upgrade note

After upgrading, supervised ELWA execution intentionally defaults OFF. Review the device readiness/ownership state and deliberately re-enable real execution when ready.
