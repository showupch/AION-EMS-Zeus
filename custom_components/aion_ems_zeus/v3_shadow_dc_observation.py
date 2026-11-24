"""Read-only array DC power observations; no controls, no energy extrapolation."""
from math import isfinite

def dc_power_observation(arrays, get_state):
    records=[]
    for array in arrays:
        name=str(array.get("name") or "Array")
        entity=array.get("power_entity")
        row={"name":name,"azimuth_deg":array.get("azimuth_deg"),
             "entity_id":entity if isinstance(entity,str) else None,
             "status":"not_configured","dc_power_w":None}
        if not isinstance(entity,str) or not entity.startswith("sensor."):
            records.append(row);continue
        state=get_state(entity)
        if state is None or state.state in ("unknown","unavailable","none",""):
            row["status"]="unavailable";records.append(row);continue
        if state.attributes.get("unit_of_measurement") not in ("W","kW"):
            row["status"]="invalid_unit";records.append(row);continue
        try:
            val=float(state.state)
            if not isfinite(val) or val<0:raise ValueError("invalid")
            if state.attributes["unit_of_measurement"]=="kW":val*=1000
            row["dc_power_w"]=round(val,2)
            row["status"]="available"
        except (ValueError,TypeError,OverflowError):
            row["status"]="invalid_value"
        records.append(row)
    total=sum(x["dc_power_w"] for x in records if x["dc_power_w"] is not None)
    complete=bool(records) and all(x["status"]=="available" for x in records)
    return {"status":"complete" if complete else "partial_or_unavailable",
            "sample_type":"instantaneous_dc_power_not_energy",
            "arrays":records,"total_dc_power_w":round(total,2) if complete else None,
            "measured_hourly_kwh":None,
            "note":"Instantaneous W readings cannot establish hourly kWh without time-series integration."}
