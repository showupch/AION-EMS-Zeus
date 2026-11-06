# AION EMS Zeus v16.0.478 — Multilingual UI & Configuration Release

## Highlights
- English remains the default interface language; German can be selected manually and is retained.
- German translations expanded across Finance, Statistics (Solar and Battery), Energy Finance, and Configuration.
- Configuration translation improvements cover Settings, Rooms & Groups, Plugins & Integrations, Entity Authorities, and Switch Hub, including status messages, forms, and explanatory text.
- Settings improvements cover Data Control, Backup & Restore, Appearance, and animation preferences.
- Consolidated release documentation and development-history notes.

## Compatibility and behavior
- No intentional changes to energy calculations, forecasts, tariffs, device control, or stored configuration values.
- Entity IDs, registered device names, and technical source identifiers are preserved.

## Installation
- **Tag:** `v16.0.478`
- **Title:** `AION EMS Zeus v16.0.478 — Multilingual UI & Configuration Release`
- **Release type:** Stable / full release (do not mark as prerelease).
- **Asset:** `aion_ems_zeus.zip` (integration files, including `manifest.json`, at ZIP root).
- Restart Home Assistant after updating; confirm version 16.0.478 in the interface and Diagnostics.

## Validation
- JavaScript syntax, Python syntax, version alignment, and ZIP integrity verified during release packaging.
- Settings translation was user-confirmed in Home Assistant. A full automated Home Assistant integration test suite was not run in this packaging environment.

Thank you to everyone testing Zeus and reporting translation gaps.
