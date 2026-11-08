"""Conservative heat-pump electrical circuit accounting.

Never add a whole-system meter to an active circuit measurement.  A mapped
cooling field with a whole-system-like entity ID is treated as ambiguous,
not as an independent circuit.  The mapping is preserved for user review.
"""
from __future__ import annotations


def is_whole_system_meter(entity_id: str | None) -> bool:
    name = str(entity_id or '').lower()
    return any(token in name for token in (
        'power_consumption_system', 'system_power_consumption',
        'total_power_consumption', 'power_consumption_total',
        'gesamtleistung', 'gesamt_leistung', 'whole_system_power',
    ))


def choose_circuit_power(heating_w, dhw_w, cooling_w, cooling_entity=None, mode=None):
    """Return (watts or None, policy, warning) from independently measured circuits.

    An ambiguous system meter must never be added to other circuit values.
    If the cooling meter is ambiguous and heating/DHW measurements are absent,
    return None instead of silently treating the system meter as cooling.
    """
    h = None if heating_w is None else max(0., float(heating_w))
    d = None if dhw_w is None else max(0., float(dhw_w))
    c = None if cooling_w is None else max(0., float(cooling_w))
    ambiguous = c is not None and is_whole_system_meter(cooling_entity)
    warning = ('Cooling electrical power mapping appears to be a whole-system meter; '
               'excluded from circuit sum. Review cooling mapping.') if ambiguous else None
    if ambiguous:
        c = None
    vals = [x for x in (h, d, c) if x is not None]
    return (sum(vals), 'split_sum_excluding_system_meter' if ambiguous else 'split_sum', warning) if vals else (None, 'unavailable', warning)
