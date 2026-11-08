# AION EMS Zeus v16.0.501 — Heat Pump Overlap Guard

- Prevents a whole-system-like electrical power sensor mapped as cooling from being summed with independent heating/DHW power circuits.
- Aligns Energy Flow and Heat Pump Intelligence source selection for the affected configuration.
- Adds `mapping_warning` and `cooling_whole_system_overlap_suspected` to the heat-pump source audit.
- Preserves registered device mappings, including the suspicious cooling mapping, for explicit user review.
- Does not alter direct COP sensors, forecast V2/V2.1, device control or energy history.
- Conservative entity-name-based guard: the reporter should still correct the cooling mapping; other ambiguous meter names may require additional evidence.
