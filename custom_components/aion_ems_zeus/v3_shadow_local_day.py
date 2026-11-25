"""V79 read-only Swiss calendar day energy from hourly end-stamped radiation."""
from datetime import timedelta
from zoneinfo import ZoneInfo
from .v3_shadow_hourly import evaluate_hour,InvalidHourlyMean,provider_hours
from .v3_shadow_day_export import day_bounds, parse_day

def local_day(payload, day, tz_name="Europe/Zurich"):
    try:
        begin, stop = day_bounds(parse_day(day), ZoneInfo(tz_name))
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidHourlyMean("invalid local date or timezone") from exc
    h, index = provider_hours(payload)
    energy=0.;count=0;cur=begin+timedelta(hours=1)
    while cur<=stop:
        i=index.get(cur)
        if i is None:raise InvalidHourlyMean("missing local-day UTC hour")
        r=evaluate_hour(h["shortwave_radiation"][i],h["direct_normal_irradiance"][i],
                        h["diffuse_radiation"][i],cur,payload.get("latitude"),payload.get("longitude"))
        energy+=min(24.,.8*12/1000*(r["east_poa_w_m2"]+r["west_poa_w_m2"]))
        count+=1;cur+=timedelta(hours=1)
    return {"status":"experimental_unvalidated","date_local":day,"experimental_kwh":round(energy,3),"hours":count}
