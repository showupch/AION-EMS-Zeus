"""Offline exploratory hourly-mean irradiance model. NOT forecast-as-issued evidence."""
from datetime import datetime,timedelta,timezone
from math import cos,radians,isfinite
from .v3_shadow_geometry import solar_position


class InvalidHourlyMean(ValueError):pass

def evaluate_hour(ghi,dni,dhi,end_utc,latitude,longitude,*,samples=60,tolerance_w_m2=2.0):
    if not isinstance(end_utc,datetime) or end_utc.tzinfo is None or end_utc.utcoffset()!=timedelta(0):raise InvalidHourlyMean('UTC required')
    if samples<2 or samples>360 or isinstance(samples,bool):raise InvalidHourlyMean('bad samples')
    for v in (ghi,dni,dhi):
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not isfinite(v) or not 0<=v<=2000:raise InvalidHourlyMean('invalid irradiance')
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
    direct=ghi-dhi
    lower=dni*min(angles)-tolerance_w_m2
    upper=dni*max(angles)+tolerance_w_m2
    if not lower<=direct<=upper:raise InvalidHourlyMean('horizontal radiation outside solar-angle interval bounds')
    return {'east_poa_w_m2':sum(east)/samples,'west_poa_w_m2':sum(west)/samples,'direct_horizontal_w_m2':direct,'min_possible_direct_w_m2':lower,'max_possible_direct_w_m2':upper}

def math_sin(x):
    from math import sin
    return sin(x)

def audit_day(payload,target_date):
    if not isinstance(payload,dict) or payload.get('timezone')!='GMT' or payload.get('utc_offset_seconds')!=0:raise InvalidHourlyMean('provider UTC metadata required')
    h=payload.get('hourly');keys=('time','shortwave_radiation','direct_normal_irradiance','diffuse_radiation')
    if not isinstance(h,dict) or any(not isinstance(h.get(k),list) for k in keys) or len({len(h[k]) for k in keys})!=1:raise InvalidHourlyMean('invalid columns')
    day=datetime.strptime(target_date,'%Y-%m-%d').replace(tzinfo=timezone.utc)
    by_time={}
    for i,raw in enumerate(h['time']):
        if not isinstance(raw,str):raise InvalidHourlyMean('bad timestamp')
        try:ts=datetime.fromisoformat(raw)
        except ValueError as e:raise InvalidHourlyMean('bad timestamp') from e
        if ts.tzinfo is not None:raise InvalidHourlyMean('unexpected offset in UTC response')
        ts=ts.replace(tzinfo=timezone.utc)
        if ts in by_time:raise InvalidHourlyMean('duplicate timestamp')
        by_time[ts]=i
    rows=[];errors=[]
    for hour in range(24):
        ts=day+timedelta(hours=hour)
        if ts not in by_time:raise InvalidHourlyMean('missing UTC hour')
        i=by_time[ts]
        try:
            result=evaluate_hour(h['shortwave_radiation'][i],h['direct_normal_irradiance'][i],h['diffuse_radiation'][i],ts,payload['latitude'],payload['longitude'])
            rows.append({'timestamp_utc':ts.isoformat().replace('+00:00','Z'),'east_poa_w_m2':result['east_poa_w_m2'],'west_poa_w_m2':result['west_poa_w_m2']})
        except (InvalidHourlyMean,ValueError,TypeError) as e:errors.append({'hour_utc':hour,'reason':str(e)})
    if errors:return {'status':'rejected','date':target_date,'rejections':errors,'experimental_kwh':None}
    # Do not call the V52 calculator with an invented issue time: it would falsely imply a forecast-as-issued.
    energy=sum(min(24.,(.8*12/1000)*(r['east_poa_w_m2']+r['west_poa_w_m2'])) for r in rows)
    return {'status':'experimental_unvalidated','date':target_date,'experimental_kwh':round(energy,3),'assumed_pr':0.8,'forecast_issue_time':None,'capture_provenance_verified':False,'forecast_accuracy_evaluated':False,'rows':rows}
