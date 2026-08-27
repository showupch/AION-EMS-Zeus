# AION EMS Zeus v14.8.2

## Heat Pump / Kiosk refinement
- Adaptive Heat Pump mapping UI from the v14.8.2 testing line is included.
- Separate Heating / DHW and Cooling measurement mappings remain optional and migration-safe.
- Heat Pump auto-detect now prefers a binary compressor-status entity when available (including Vitocal Kompressor Status patterns).
- When a Heat Pump provides DHW and no standalone Water Heater is registered, Kiosk integrates DHW evidence into the Heat Pump card instead of presenting a separate external-water-heater style block.
- Installations with a standalone Water Heater / ELWA keep the existing separate Water Heater Kiosk card unchanged.

## Configuration UX
- Device Identity / Primary Measurements layout.
- Optional Evidence and advanced device sections are collapsed by default.
- Smart Control & Safety is only shown where relevant.
- Registered Device cards remain compact and stable.
- Daily-reset energy sensors are presented with daily semantics when clearly identifiable.

## Home Assistant sidebar
- Panel/sidebar label corrected from `AION EMS Energy Flow` to `AION EMS Zeus` without changing the existing panel path.

## Non-regression
- Validated ELWA closed-loop control is unchanged.
- Existing Command Center layout for installations with a standalone ELWA / Water Heater is preserved.
