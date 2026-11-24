"""Offline deterministic NOAA-style solar position and isotropic-sky POA prototype.
No weather retrieval, training, calibration, or production integration.
Azimuth: 0 north, 90 east, 180 south, 270 west. UTC timestamps only.
"""
from datetime import datetime, timezone
import math

def solar_position(timestamp_utc, latitude_deg, longitude_deg):
    if not isinstance(timestamp_utc, datetime) or timestamp_utc.tzinfo is None or timestamp_utc.utcoffset().total_seconds()!=0:
        raise ValueError('UTC-aware timestamp required')
    if not all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in (latitude_deg,longitude_deg)) or not -90<=latitude_deg<=90 or not -180<=longitude_deg<=180:
        raise ValueError('invalid coordinates')
    utc=timestamp_utc.astimezone(timezone.utc)
    doy=utc.timetuple().tm_yday
    hour=utc.hour+utc.minute/60+utc.second/3600+utc.microsecond/3.6e9
    gamma=2*math.pi/365*(doy-1+(hour-12)/24)
    eqtime=229.18*(0.000075+0.001868*math.cos(gamma)-0.032077*math.sin(gamma)-0.014615*math.cos(2*gamma)-0.040849*math.sin(2*gamma))
    decl=(0.006918-0.399912*math.cos(gamma)+0.070257*math.sin(gamma)-0.006758*math.cos(2*gamma)+0.000907*math.sin(2*gamma)-0.002697*math.cos(3*gamma)+0.00148*math.sin(3*gamma))
    ha=math.radians(((hour*60+eqtime+4*longitude_deg)/4-180+180)%360-180)
    lat=math.radians(latitude_deg)
    coszen=max(-1.,min(1.,math.sin(lat)*math.sin(decl)+math.cos(lat)*math.cos(decl)*math.cos(ha)))
    zen=math.acos(coszen)
    elevation=90-math.degrees(zen)
    east=-math.cos(decl)*math.sin(ha)
    north=math.cos(lat)*math.sin(decl)-math.sin(lat)*math.cos(decl)*math.cos(ha)
    az=(math.degrees(math.atan2(east,north))+360)%360
    return {'elevation_deg':elevation,'zenith_deg':math.degrees(zen),'azimuth_deg':az,'sun_up':elevation>0}

def isotropic_poa(ghi_w_m2,dni_w_m2,dhi_w_m2,solar_zenith_deg,solar_azimuth_deg,tilt_deg,surface_azimuth_deg,albedo=0.2):
    vals=(ghi_w_m2,dni_w_m2,dhi_w_m2,solar_zenith_deg,solar_azimuth_deg,tilt_deg,surface_azimuth_deg,albedo)
    if any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in vals):raise ValueError('nonfinite input')
    if not (0<=ghi_w_m2<=2000 and 0<=dni_w_m2<=2000 and 0<=dhi_w_m2<=2000 and 0<=solar_zenith_deg<=180 and 0<=solar_azimuth_deg<=360 and 0<=tilt_deg<=90 and 0<=surface_azimuth_deg<=360 and 0<=albedo<=1):raise ValueError('out of range')
    if dhi_w_m2>ghi_w_m2+1e-9:raise ValueError('DHI exceeds GHI')
    # V56: physically consistent horizontal irradiance; fail closed.
    # 1 W/m2 absolute tolerance accounts for provider rounding only.
    if solar_zenith_deg>=90:
        if any(v>1.0 for v in (ghi_w_m2,dni_w_m2,dhi_w_m2)):
            raise ValueError('nonzero irradiance at night')
        return 0.0
    expected_ghi=dhi_w_m2+dni_w_m2*max(0.0,math.cos(math.radians(solar_zenith_deg)))
    if abs(ghi_w_m2-expected_ghi)>1.0:
        raise ValueError('GHI/DNI/DHI inconsistent with solar zenith')
    z=math.radians(solar_zenith_deg); b=math.radians(tilt_deg); delta=math.radians(solar_azimuth_deg-surface_azimuth_deg)
    incidence=max(0.,math.cos(z)*math.cos(b)+math.sin(z)*math.sin(b)*math.cos(delta))
    return dni_w_m2*incidence+dhi_w_m2*(1+math.cos(b))/2+ghi_w_m2*albedo*(1-math.cos(b))/2
