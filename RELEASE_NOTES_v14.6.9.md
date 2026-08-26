# AION EMS Zeus v14.6.9 — Heat Pump Persistence Hardening

Built cumulatively from v14.6.8.

- Hardens Registered Device updates so fields omitted by an older/cached frontend no longer erase existing Heat Pump mappings.
- Adds a no-default update schema so omitted metadata is preserved rather than silently reset.
- Normalizes older 14.4.x Heat Pump registry records with explicit null Advanced Heat Pump fields, making configuration exports schema-stable.
- Existing Advanced Heat Pump values are always preserved during migration.
- This addresses the persistence/export scenario reported by Martin while remaining backward-compatible with older stored device records.
- Kiosk frontend/geometry and Home Energy Timeline are unchanged.
