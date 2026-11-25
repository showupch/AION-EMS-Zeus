"""Offline exploratory hourly-mean irradiance model. NOT forecast-as-issued evidence."""
from datetime import datetime,timedelta,timezone
from math import cos,radians,isfinite
from .v3_shadow_geometry import solar_position


class InvalidHourlyMean(ValueError):pass

def evaluate_hour(ghi,dni,dhi,end_utc,latitude,longitude,*,samples=60,tolerance_w_m2=2.0):
    if not isinstance(end_utc,datetime) or end_utc.tzinfo is None or end_utc.utcoffset()!=timedelta(0):raise InvalidHourlyMean('UTC required')
    if type(samples) is not int or not 2 <= samples <= 360:raise InvalidHourlyMean('bad samples')
    if end_utc != end_utc.replace(minute=0, second=0, microsecond=0):
        raise InvalidHourlyMean("exact UTC hour required")
    if type(tolerance_w_m2) not in (int, float) or not 0 <= tolerance_w_m2 <= 2000:
        raise InvalidHourlyMean("invalid irradiance tolerance")
    for v in (ghi,dni,dhi):
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not 0<=v<=2000 or not isfinite(v):raise InvalidHourlyMean('invalid irradiance')
    if dhi>ghi+tolerance_w_m2:raise InvalidHourlyMean('DHI greater than GHI')
    angles=[];east=[];west=[]
    for i in range(samples):
        t=end_utc-timedelta(hours=1)+timedelta(seconds=3600*(i+.5)/samples)
        pos=solar_position(t,latitude,longitude)
        z=radians(pos['zenith_deg']);a=radians(pos['azimuth_deg']);b=radians(33)
        cz=max(0.,cos(z));angles.append(cz)
        for az,acc in ((90,east),(270,west)):
            inc=max(0.,cos(z)*cos(b)+math_sin(z)*math_sin(b)*cos(a-radians(az))) if cz>0 else 0.
            acc.append(dni*inc+dhi*(1+cos(b))/2*(1 if cz>0 else 0)+ghi*.2*(1-cos(b))/2*(1 if cz>0 else 0))
    if not any(angles) and max(ghi, dni, dhi) > tolerance_w_m2:
        raise InvalidHourlyMean("nonzero irradiance in a fully dark hour")
    direct=ghi-dhi
    lower=dni*min(angles)-tolerance_w_m2
    upper=dni*max(angles)+tolerance_w_m2
    if not lower<=direct<=upper:raise InvalidHourlyMean('horizontal radiation outside solar-angle interval bounds')
    return {'east_poa_w_m2':sum(east)/samples,'west_poa_w_m2':sum(west)/samples,'direct_horizontal_w_m2':direct,'min_possible_direct_w_m2':lower,'max_possible_direct_w_m2':upper}

def math_sin(x):
    from math import sin
    return sin(x)

def provider_hours(payload):
    if (not isinstance(payload, dict) or payload.get("timezone") != "GMT"
            or type(payload.get("utc_offset_seconds")) is not int or payload["utc_offset_seconds"] != 0):
        raise InvalidHourlyMean("provider UTC metadata required")
    h = payload.get("hourly")
    keys = ("time", "shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation")
    if (not isinstance(h, dict) or any(not isinstance(h.get(key), list) for key in keys)
            or len({len(h[key]) for key in keys}) != 1):
        raise InvalidHourlyMean("invalid hourly columns")
    by_time = {}
    for index, raw in enumerate(h["time"]):
        try:
            if not isinstance(raw, str) or len(raw) not in (16, 19) or raw[10] != "T":
                raise ValueError
            stamp = datetime.fromisoformat(raw)
            if stamp.tzinfo is not None or stamp.minute or stamp.second or stamp.microsecond:
                raise ValueError
        except ValueError as exc:
            raise InvalidHourlyMean("invalid UTC hour timestamp") from exc
        stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp in by_time:
            raise InvalidHourlyMean("duplicate UTC hour")
        by_time[stamp] = index
    return h, by_time


def audit_day(payload,target_date):
    h, by_time = provider_hours(payload)
    day = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    rows=[];errors=[]
    for hour in range(24):
        ts=day+timedelta(hours=hour)
        if ts not in by_time:raise InvalidHourlyMean('missing UTC hour')
        i=by_time[ts]
        try:
            result=evaluate_hour(h['shortwave_radiation'][i],h['direct_normal_irradiance'][i],h['diffuse_radiation'][i],ts,payload.get('latitude'),payload.get('longitude'))
            rows.append({'timestamp_utc':ts.isoformat().replace('+00:00','Z'),'east_poa_w_m2':result['east_poa_w_m2'],'west_poa_w_m2':result['west_poa_w_m2']})
        except (InvalidHourlyMean,ValueError,TypeError) as e:errors.append({'hour_utc':hour,'reason':str(e)})
    if errors:return {'status':'rejected','date':target_date,'rejections':errors,'experimental_kwh':None}
    # Do not call the V52 calculator with an invented issue time: it would falsely imply a forecast-as-issued.
    energy=sum(min(24.,(.8*12/1000)*(r['east_poa_w_m2']+r['west_poa_w_m2'])) for r in rows)
    return {'status':'experimental_unvalidated','date':target_date,'experimental_kwh':round(energy,3),'assumed_pr':0.8,'forecast_issue_time':None,'capture_provenance_verified':False,'forecast_accuracy_evaluated':False,'rows':rows}
