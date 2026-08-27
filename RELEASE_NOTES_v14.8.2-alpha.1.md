# AION EMS Zeus v14.8.2-alpha.1

## Adaptive Heat Pump Mapping UI

- Keeps the simple whole-unit Heat Pump mapping as the default view.
- Adds **Separate Heating / DHW measurements available**. When enabled, Zeus exposes circuit-specific Electrical Power, Thermal Power, Electrical Energy and Thermal Energy fields for Space Heating and DHW.
- Adds **Cooling measurements available** as a separate switch.
- Existing v14.8.1 circuit mappings automatically enable the corresponding advanced section during migration.
- Heat-carrier, temperature, compressor, operating-mode and DHW temperature evidence remain visible independently of the advanced measurement switches.
- Legacy / Unclassified Energy Mappings remain migration-safe and only appear when legacy mappings actually exist.
- No circuit-specific COP calculations are added yet; this build is for mapping/UI validation first.
- The validated ELWA control path is unchanged.
