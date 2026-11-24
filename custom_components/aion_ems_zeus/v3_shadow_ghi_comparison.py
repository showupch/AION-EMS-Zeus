"""Read-only comparison of two hourly mean GHI weather forecast sources.

Both providers' timestamps denote the END of the represented hourly interval.
No PV generation, control decisions, calibration, or forecast accuracy claims.
"""
from datetime import datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

def compare_ghi(open_meteo, swiss_rows, target_date, timezone_name="Europe/Zurich"):
    zone=ZoneInfo(timezone_name)
    start=datetime.fromisoformat(target_date).replace(tzinfo=zone).astimezone(timezone.utc)
    stop=(datetime.fromisoformat(target_date).replace(tzinfo=zone)+timedelta(days=1)).astimezone(timezone.utc)
    expected=[]
    ts=start+timedelta(hours=1)
    while ts<=stop:
        expected.append(ts)
        ts+=timedelta(hours=1)
    result={"status":"insufficient_aligned_data","target_date_local":target_date,
            "expected_hours":len(expected),"matched_hours":0,
            "timestamp_semantics":"preceding_hour_mean_end_time",
            "units":"W/m2 hourly means; daily Wh/m2",
            "comparison_is_forecast_accuracy":False}
    if not isinstance(open_meteo,dict) or open_meteo.get("timezone")!="GMT" or open_meteo.get("utc_offset_seconds")!=0:
        result["reason"]="Open-Meteo UTC metadata invalid";return result
    hourly=open_meteo.get("hourly",{})
    if not isinstance(hourly,dict) or not isinstance(hourly.get("time"),list) or not isinstance(hourly.get("shortwave_radiation"),list) or len(hourly["time"])!=len(hourly["shortwave_radiation"]):
        result["reason"]="Open-Meteo hourly arrays invalid";return result
    om={}
    for stamp,val in zip(hourly["time"],hourly["shortwave_radiation"]):
        try:
            dt=datetime.fromisoformat(stamp)
            if dt.tzinfo is not None or not isinstance(val,(int,float)) or isinstance(val,bool) or not isfinite(val) or val<0:continue
            key=dt.replace(tzinfo=timezone.utc)
            if key in om:result["reason"]="duplicate Open-Meteo hour";return result
            om[key]=float(val)
        except (ValueError,TypeError):continue
    swiss={}
    for row in swiss_rows if isinstance(swiss_rows,list) else []:
        if not isinstance(row,dict):continue
        val=row.get("global_radiation")
        try:
            dt=datetime.fromisoformat(row["datetime"])
            if dt.tzinfo is None or not isinstance(val,(int,float)) or isinstance(val,bool) or not isfinite(val) or val<0:continue
            key=dt.astimezone(timezone.utc)
            if key in swiss:result["reason"]="duplicate MeteoSwiss hour";return result
            swiss[key]=float(val)
        except (KeyError,TypeError,ValueError):continue
    pairs=[(om[t],swiss[t]) for t in expected if t in om and t in swiss]
    result["matched_hours"]=len(pairs)
    result["open_meteo_hours"]=sum(t in om for t in expected)
    result["meteoswiss_hours"]=sum(t in swiss for t in expected)
    if len(pairs)!=len(expected):
        result["reason"]="incomplete hourly overlap; metrics suppressed";return result
    om_total=sum(a for a,b in pairs)
    sw_total=sum(b for a,b in pairs)
    delta=om_total-sw_total
    result.update(status="aligned",open_meteo_ghi_wh_m2=round(om_total,2),
                  meteoswiss_ghi_wh_m2=round(sw_total,2),
                  difference_wh_m2=round(delta,2),
                  difference_pct_of_meteoswiss=round(100*delta/sw_total,2) if sw_total>0 else None,
                  mean_absolute_difference_w_m2=round(sum(abs(a-b) for a,b in pairs)/len(pairs),2),
                  mean_signed_difference_w_m2=round(delta/len(pairs),2),
                  daylight_matched_hours=sum(a>0 or b>0 for a,b in pairs))
    return result
