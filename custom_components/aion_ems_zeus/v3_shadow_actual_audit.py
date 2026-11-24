"""V82 conservative, read-only forecast-to-measurement evidence audit."""
from datetime import date, datetime, timezone
from math import isfinite

AUTHORITATIVE_METHOD = "home_assistant_energy_source_set_statistics"

def compare_completed_days(snapshots, rows, local_today):
    """Only use pre-day forecasts and HA Energy-source-set daily statistics."""
    result={"status":"awaiting_verified_actual","comparison_is_accuracy_validated":False,
            "eligible_days":0,"compared_days":0,"latest_comparison":None,
            "source_required":AUTHORITATIVE_METHOD}
    if not isinstance(snapshots,list) or not isinstance(rows,list):
        result["reason"]="missing_snapshots_or_analytics";return result
    candidates={}
    rejected={"malformed_or_invalid":0,"target_not_completed":0,
              "not_retrieved_before_target_day":0}
    for snapshot in snapshots:
        if not isinstance(snapshot,dict):
            rejected["malformed_or_invalid"]+=1
            continue
        day=snapshot.get("target_date_local")
        try:
            target=date.fromisoformat(day)
            retrieval=datetime.fromisoformat(snapshot["retrieved_at_utc"])
            value=float(snapshot["forecast_v3_kwh"])
            if retrieval.tzinfo is None or not isfinite(value) or value<0:
                rejected["malformed_or_invalid"]+=1
                continue
            if target>=local_today:
                rejected["target_not_completed"]+=1
                continue
            # Forecast must have been retrieved BEFORE the target day starts,
            # using UTC date as conservative boundary (Swiss local day starts later).
            if retrieval.astimezone(timezone.utc).date()>=target:
                rejected["not_retrieved_before_target_day"]+=1
                continue
        except (ValueError,TypeError,KeyError,OverflowError):
            rejected["malformed_or_invalid"]+=1
            continue
        # Use last qualifying forecast; do not average or fit forecasts to actuals.
        key=day
        if key not in candidates or retrieval>candidates[key][0]:
            candidates[key]=(retrieval,value)
    result["eligible_days"]=len(candidates)
    result["snapshot_count_checked"]=len(snapshots)
    result["snapshot_rejection_counts"]=rejected
    result["eligible_target_dates"]=sorted(candidates)[-7:]
    actual_dates=set()
    missing_method=0
    missing_source=0
    for row in rows:
        if not isinstance(row,dict):continue
        day=row.get("date")
        try:
            if date.fromisoformat(day)>=local_today:continue
            value=float(row.get("solar_energy_kwh"))
            if not isfinite(value) or value<0:continue
        except (TypeError,ValueError,OverflowError):continue
        if row.get("solar_energy_kwh_method")!=AUTHORITATIVE_METHOD:
            missing_method+=1
            continue
        if not row.get("solar_energy_kwh_source"):
            missing_source+=1
            continue
        actual_dates.add(day)
    result["authoritative_actual_days"]=len(actual_dates)
    result["authoritative_actual_dates"]=sorted(actual_dates)[-7:]
    result["eligible_forecasts_without_authoritative_actual"]=len(set(candidates)-actual_dates)
    result["authoritative_actuals_without_eligible_forecast"]=len(actual_dates-set(candidates))
    result["non_authoritative_actual_rows"]=missing_method
    result["actual_rows_missing_source"]=missing_source
    matches=[]
    for row in rows:
        if not isinstance(row,dict):continue
        day=row.get("date")
        if day not in candidates:continue
        if row.get("solar_energy_kwh_method")!=AUTHORITATIVE_METHOD:continue
        if not row.get("solar_energy_kwh_source"):continue
        try:
            actual=float(row["solar_energy_kwh"])
            if not isfinite(actual) or actual<0:continue
        except (ValueError,TypeError,KeyError):continue
        retrieved,predicted=candidates[day]
        matches.append({"date_local":day,"forecast_kwh":round(predicted,3),
                        "actual_kwh":round(actual,3),
                        "error_kwh":round(predicted-actual,3),
                        "error_pct":round(100*(predicted-actual)/actual,2) if actual>0 else None,
                        "retrieved_at_utc":retrieved.isoformat(),
                        "actual_source":row["solar_energy_kwh_source"],
                        "actual_method":AUTHORITATIVE_METHOD,
                        "provider_issue_time_verified":False})
    matches.sort(key=lambda x:x["date_local"])
    result["compared_days"]=len(matches)
    if matches:
        result["status"]="exploratory_comparison_available"
        result["latest_comparison"]=matches[-1]
        result["mean_absolute_error_kwh"]=round(sum(abs(x["error_kwh"]) for x in matches)/len(matches),3)
        result["note"]="Retrieved-before-day evidence only; provider issue time unverified; not validated accuracy."
    else:
        if not candidates:
            result["reason"]="No completed-day pre-day V3 snapshots qualify; inspect snapshot_rejection_counts."
        elif not actual_dates:
            result["reason"]="No authoritative HA Energy solar source-set daily statistics available."
        else:
            result["reason"]="Eligible forecast dates and authoritative actual dates do not overlap."
    return result
