# AION EMS Zeus v16.0.479 — Chart clarity and split-source safeguard

- Monthly energy timeline now explicitly says **Month-to-date**, clarifying why the first seven days of November match a seven-day chart. No historical energy data or period boundaries were changed.
- Split heat-pump electrical power accounting no longer adds the same entity ID twice if a user maps one sensor to multiple circuit fields. Applied consistently to energy flow and device analytics.
- Distinct heating, DHW and cooling sensors remain additive. The reported 860 W versus 1480 W discrepancy is **not confirmed fixed**: it requires circuit-source semantics or simultaneous sensor readings to establish whether DHW measurements overlap. No speculative power scaling was applied.
- Production forecasting, V2.1 shadow and device control are unchanged.

Development test build; not a published release.
