# AION EMS Zeus v14.6.7 — Timeline Repeat-Transition Hotfix

Built cumulatively from v14.6.6.

- Fixes repeated Timeline events being suppressed within the same hour.
- Event deduplication now uses minute granularity instead of hour granularity.
- Supports EV/device sequences such as started -> stopped -> started again in one hour.
- Timeline persistence and live event refresh remain unchanged.
- Locked v14.6.5 kiosk frontend remains byte-for-byte unchanged.
- No Energy Flow, finance, Heat Pump intelligence, analytics, or device-control changes.
