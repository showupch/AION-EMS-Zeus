"""Read-only V3 per-array geometry model; no controls or V2 writes."""
from datetime import datetime,timedelta,timezone
from math import cos,sin,radians,isfinite
from .v3_shadow_geometry import solar_position
from .v3_shadow_hourly import evaluate_hour, InvalidHourlyMean

def validate_arrays(raw):
    if not isinstance(raw,list) or not 1<=len(raw)<=12:
        raise ValueError("1-12 saved PV arrays required")
    arrays=[]
    for item in raw:
        if not isinstance(item,dict):raise ValueError("invalid array record")
        vals=[]
        for field in ("capacity_kwp","azimuth_deg","tilt_deg"):
            value=item.get(field)
            if isinstance(value,bool) or not isinstance(value,(float,int)) or not isfinite(value):
                raise ValueError("invalid array "+field)
            vals.append(float(value))
        cap,az,tilt=vals
        if not (0<cap<=1000 and 0<=az<360 and 0<=tilt<=90):
            raise ValueError("array geometry out of range")
        arrays.append({"name":str(item.get("name") or "Array")[:80],
                       "capacity_kwp":cap,"azimuth_deg":az,"tilt_deg":tilt})
    return arrays

def hourly_poa(ghi,dni,dhi,end_utc,latitude,longitude,arrays,samples=60):
    # Run the existing hourly-mean physical consistency gate first.
    evaluate_hour(ghi,dni,dhi,end_utc,latitude,longitude,samples=samples)
    accum=[0.0]*len(arrays)
    for i in range(samples):
        instant=end_utc-timedelta(hours=1)+timedelta(seconds=3600*(i+.5)/samples)
        pos=solar_position(instant,latitude,longitude)
        zen=radians(pos["zenith_deg"]);az=radians(pos["azimuth_deg"])
        if cos(zen)<=0:continue
        for j,array in enumerate(arrays):
            tilt=radians(array["tilt_deg"])
            facing=radians(array["azimuth_deg"])
            incident=max(0.,cos(zen)*cos(tilt)+sin(zen)*sin(tilt)*cos(az-facing))
            poa=dni*incident+dhi*(1+cos(tilt))/2+ghi*.2*(1-cos(tilt))/2
            accum[j]+=poa/samples
    return accum

def array_day(payload,day,tz_name,raw_arrays,performance_ratio=.8):
    from zoneinfo import ZoneInfo
    arrays=validate_arrays(raw_arrays)
    if payload.get("timezone")!="GMT" or payload.get("utc_offset_seconds")!=0:
        raise InvalidHourlyMean("provider UTC metadata required")
    h=payload.get("hourly")
    keys=("time","shortwave_radiation","direct_normal_irradiance","diffuse_radiation")
    if not isinstance(h,dict) or any(not isinstance(h.get(k),list) for k in keys) or len({len(h[k]) for k in keys})!=1:
        raise InvalidHourlyMean("invalid hourly columns")
    zone=ZoneInfo(tz_name)
    start=datetime.fromisoformat(day).replace(tzinfo=zone)
    begin=start.astimezone(timezone.utc)
    end=(start+timedelta(days=1)).astimezone(timezone.utc)
    by_time={}
    for i,stamp in enumerate(h["time"]):
        try:ts=datetime.fromisoformat(stamp)
        except (ValueError,TypeError) as e:raise InvalidHourlyMean("invalid timestamp") from e
        if ts.tzinfo is not None:raise InvalidHourlyMean("unexpected offset")
        ts=ts.replace(tzinfo=timezone.utc)
        if ts in by_time:raise InvalidHourlyMean("duplicate timestamp")
        by_time[ts]=i
    totals=[0.0]*len(arrays);hourly=[];hours=0;cur=begin+timedelta(hours=1)
    direct_hours=0; direct_irradiance_sum=0.0; daylight_hours=0; directional_difference_hours=0
    while cur<=end:
        i=by_time.get(cur)
        if i is None:raise InvalidHourlyMean("missing local-day hour")
        try:
            poas=hourly_poa(h["shortwave_radiation"][i],h["direct_normal_irradiance"][i],
                            h["diffuse_radiation"][i],cur,payload["latitude"],payload["longitude"],arrays)
        except (InvalidHourlyMean, ValueError, TypeError) as exc:
            # Diagnostic only: never repair or substitute rejected provider values.
            from .v3_shadow_weather_diagnostics import WeatherInputRejected
            raise WeatherInputRejected(
                hour_end_utc=cur.isoformat(), reason=str(exc),
                ghi=h["shortwave_radiation"][i],
                dni=h["direct_normal_irradiance"][i],
                dhi=h["diffuse_radiation"][i],
            ) from exc
        dni=h["direct_normal_irradiance"][i]
        ghi=h["shortwave_radiation"][i]
        if ghi>1:daylight_hours+=1
        if dni>1:direct_hours+=1
        direct_irradiance_sum+=dni
        if len(poas)>=2 and abs(poas[0]-poas[1])>0.01:
            directional_difference_hours+=1
        values=[performance_ratio*arrays[j]["capacity_kwp"]*poa/1000
                for j,poa in enumerate(poas)]
        for j,value in enumerate(values):
            totals[j]+=value
        hourly.append({"hour_end_utc":cur.isoformat(),
                       "array_kwh":[round(v,4) for v in values],
                       "total_kwh":round(sum(values),4)})
        hours+=1;cur+=timedelta(hours=1)
    return {"status":"experimental_unvalidated","experimental_kwh":round(sum(totals),3),
            "hours":hours,"hourly_forecasts":hourly,
            "orientation_diagnostics":{"direct_normal_irradiance_wh_m2":round(direct_irradiance_sum,2),
                "hours_with_direct_sun":direct_hours,"hours_with_ghi":daylight_hours,
                "first_two_arrays_poa_different_hours":directional_difference_hours,
                "note":"Matching East/West output can be expected with DNI near zero; this is not an azimuth validation."},"array_forecasts":[{"name":a["name"],"capacity_kwp":a["capacity_kwp"],
             "azimuth_deg":a["azimuth_deg"],"tilt_deg":a["tilt_deg"],
             "forecast_kwh":round(v,3)} for a,v in zip(arrays,totals)],
            "total_capacity_kwp":round(sum(a["capacity_kwp"] for a in arrays),3)}
