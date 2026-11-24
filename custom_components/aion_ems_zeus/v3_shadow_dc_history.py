"""Read-only DC observations and conservative time-weighted hourly energy.

Never interpolate across missing or widely spaced readings; no extrapolation.
"""
from datetime import datetime, timezone, timedelta
from math import isfinite

MAX_GAP_SECONDS=120
MIN_COVERAGE_SECONDS=3300

def collect_sample(arrays, states, when):
    if when.tzinfo is None:raise ValueError("UTC timestamp required")
    values={}
    for array in arrays or []:
        entity=array.get("power_entity")
        if not isinstance(entity,str) or not entity.startswith("sensor."):continue
        state=states(entity)
        if state is None or state.state in ("unavailable","unknown", "none", ""):continue
        unit=state.attributes.get("unit_of_measurement")
        if unit not in ("W","kW"):continue
        try:
            value=float(state.state)*(1000 if unit=="kW" else 1)
            if not isfinite(value) or value<0:continue
        except (TypeError,ValueError,OverflowError):continue
        values[entity]=round(value,3)
    return {"time_utc":when.astimezone(timezone.utc).isoformat(),"watts":values}

def hourly_dc_energy(samples, entity_ids, now):
    """Return completed UTC-hour buckets; only sufficiently sampled hours qualify."""
    valid=[]
    for row in samples if isinstance(samples,list) else []:
        try:
            t=datetime.fromisoformat(row["time_utc"])
            if t.tzinfo is None:continue
            t=t.astimezone(timezone.utc)
            if t>now or not isinstance(row["watts"],dict):continue
            valid.append((t,row["watts"]))
        except (KeyError,ValueError,TypeError):continue
    valid.sort(key=lambda r:r[0]); buckets={}
    for (a,av),(b,bv) in zip(valid,valid[1:]):
        seconds=(b-a).total_seconds()
        if seconds<=0 or seconds>MAX_GAP_SECONDS:continue
        # Divide segments at exact UTC-hour boundaries.
        cursor=a
        while cursor<b:
            boundary=cursor.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1)
            stop=min(b,boundary)
            hour_end=boundary
            if hour_end>now:break
            fraction0=(cursor-a).total_seconds()/seconds
            fraction1=(stop-a).total_seconds()/seconds
            for entity in entity_ids:
                x=av.get(entity);y=bv.get(entity)
                if not all(isinstance(v,(int,float)) and isfinite(v) and v>=0 for v in (x,y)):continue
                w0=x+(y-x)*fraction0;w1=x+(y-x)*fraction1
                key=(hour_end.isoformat(),entity)
                energy,covered=buckets.get(key,(0.,0.))
                dt=(stop-cursor).total_seconds()
                buckets[key]=(energy+(w0+w1)*dt/7200000,covered+dt)
            cursor=stop
    result=[]
    hours=sorted(set(k[0] for k in buckets))[-24:]
    for hour in hours:
        entries=[]
        for entity in entity_ids:
            kwh,covered=buckets.get((hour,entity),(0.,0.))
            entries.append({"entity_id":entity,"coverage_seconds":round(covered),
                            "measured_kwh":round(kwh,4) if covered>=MIN_COVERAGE_SECONDS else None})
        result.append({"hour_end_utc":hour,"arrays":entries,
                       "status":"complete" if entries and all(e["measured_kwh"] is not None for e in entries) else "insufficient_coverage"})
    return result
