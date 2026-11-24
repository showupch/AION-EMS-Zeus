"""V79 read-only Swiss calendar day energy from hourly end-stamped radiation."""
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from .v3_shadow_hourly import evaluate_hour,InvalidHourlyMean

def local_day(payload, day, tz_name="Europe/Zurich"):
    try: zone=ZoneInfo(tz_name); start=datetime.fromisoformat(day).replace(tzinfo=zone)
    except (ValueError,KeyError) as e:raise InvalidHourlyMean("invalid Swiss date/timezone") from e
    stop=(start+timedelta(days=1)).astimezone(timezone.utc)
    begin=start.astimezone(timezone.utc)
    if payload.get("timezone")!="GMT" or payload.get("utc_offset_seconds")!=0:raise InvalidHourlyMean("UTC metadata required")
    h=payload.get("hourly")
    keys=("time","shortwave_radiation","direct_normal_irradiance","diffuse_radiation")
    if not isinstance(h,dict) or any(not isinstance(h.get(k),list) for k in keys) or len({len(h[k]) for k in keys})!=1:raise InvalidHourlyMean("invalid columns")
    index={}
    for i,stamp in enumerate(h["time"]):
        if not isinstance(stamp,str):raise InvalidHourlyMean("invalid time")
        try:ts=datetime.fromisoformat(stamp)
        except ValueError as e:raise InvalidHourlyMean("invalid time") from e
        if ts.tzinfo is not None:raise InvalidHourlyMean("expected naive UTC")
        ts=ts.replace(tzinfo=timezone.utc)
        if ts in index:raise InvalidHourlyMean("duplicate hour")
        index[ts]=i
    energy=0.;count=0;cur=begin+timedelta(hours=1)
    while cur<=stop:
        i=index.get(cur)
        if i is None:raise InvalidHourlyMean("missing local-day UTC hour")
        r=evaluate_hour(h["shortwave_radiation"][i],h["direct_normal_irradiance"][i],
                        h["diffuse_radiation"][i],cur,payload["latitude"],payload["longitude"])
        energy+=min(24.,.8*12/1000*(r["east_poa_w_m2"]+r["west_poa_w_m2"]))
        count+=1;cur+=timedelta(hours=1)
    return {"status":"experimental_unvalidated","date_local":day,"experimental_kwh":round(energy,3),"hours":count}
