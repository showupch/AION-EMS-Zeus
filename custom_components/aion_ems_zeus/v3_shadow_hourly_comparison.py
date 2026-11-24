"""Read-only V3 hourly comparison. Loss-adjusted forecast vs measured DC is not accuracy validation."""
from datetime import datetime,timezone

def compare_hourly_dc(snapshots, measured_hours, entity_ids, now):
    """Use earliest pre-target snapshot with saved hourly rows; never use retrospective forecasts."""
    if now.tzinfo is None: raise ValueError("timezone-aware now required")
    now=now.astimezone(timezone.utc)
    chosen={}
    for snap in snapshots if isinstance(snapshots,list) else []:
        try:
            captured=datetime.fromisoformat(snap['retrieved_at_utc']).astimezone(timezone.utc)
            target=snap['target_date_local']
            hourly=snap.get('hourly_array_forecasts')
            if not isinstance(hourly,list) or not hourly:continue
            # Date-scoped pre-day capture: reject forecasts fetched on the target day.
            from zoneinfo import ZoneInfo
            zone=ZoneInfo(snap.get('local_timezone','Europe/Zurich'))
            if captured.astimezone(zone).date().isoformat()>=target:continue
            old=chosen.get(target)
            if old is None or captured<old[0]:chosen[target]=(captured,snap)
        except (ValueError,KeyError,TypeError,OverflowError):continue
    measured={r.get('hour_end_utc'):r for r in measured_hours if isinstance(r,dict) and r.get('status')=='complete'}
    comparisons=[]
    for target,(_,snap) in chosen.items():
        for row in snap['hourly_array_forecasts']:
            try:
                stamp=row['hour_end_utc'];end=datetime.fromisoformat(stamp).astimezone(timezone.utc)
                if end>now or stamp not in measured:continue
                predicted=row['array_kwh'];observed=measured[stamp]['arrays']
                if len(predicted)!=len(entity_ids) or snap.get('array_power_entities')!=entity_ids:continue
                actual_by_id={a['entity_id']:a.get('measured_kwh') for a in observed}
                if any(actual_by_id.get(e) is None for e in entity_ids):continue
                arrays=[]
                for i,e in enumerate(entity_ids):
                    f=float(predicted[i]);a=float(actual_by_id[e]);
                    if f<0 or a<0:raise ValueError('negative energy')
                    arrays.append({'entity_id':e,'forecast_loss_adjusted_kwh':round(f,4),
                                   'measured_dc_kwh':round(a,4),'difference_kwh':round(f-a,4)})
                comparisons.append({'target_date_local':target,'hour_end_utc':stamp,'arrays':arrays,
                                    'forecast_total_kwh':round(sum(x['forecast_loss_adjusted_kwh'] for x in arrays),4),
                                    'measured_dc_total_kwh':round(sum(x['measured_dc_kwh'] for x in arrays),4)})
            except (KeyError,ValueError,TypeError,OverflowError):continue
    comparisons.sort(key=lambda x:x['hour_end_utc'])
    return {'status':'comparison_available' if comparisons else 'awaiting_matching_completed_hours',
            'comparison_count':len(comparisons),'latest_comparisons':comparisons[-8:],
            'accuracy_validated':False,'energy_basis':'forecast_loss_adjusted_vs_measured_dc',
            'warning':'Different energy bases: diagnostic comparison only, not accuracy scoring or calibration.',
            'pre_day_hourly_snapshot_targets':sorted(chosen)}
