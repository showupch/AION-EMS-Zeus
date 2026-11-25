"""V82 conservative, read-only forecast-to-measurement evidence audit."""
from datetime import date, datetime
from math import isfinite, sqrt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

AUTHORITATIVE_METHOD = "home_assistant_energy_source_set_statistics"

def valid_forecast(value):
    return type(value) in (int, float) and 0 <= value <= 1_000_000


def paired_capture(row):
    return valid_forecast(row.get("forecast_v2_kwh")) and valid_forecast(row.get("forecast_v3_kwh"))


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
            value=snapshot["forecast_v3_kwh"]
            if retrieval.utcoffset() is None or not valid_forecast(value):
                rejected["malformed_or_invalid"]+=1
                continue
            if target>=local_today:
                rejected["target_not_completed"]+=1
                continue
            # The target day starts at midnight in the capture timezone.
            if retrieval.astimezone(ZoneInfo(snapshot.get("local_timezone") or "UTC")).date()>=target:
                rejected["not_retrieved_before_target_day"]+=1
                continue
        except (ValueError,TypeError,KeyError,OverflowError,ZoneInfoNotFoundError):
            rejected["malformed_or_invalid"]+=1
            continue
        # Use paired captures for head-to-head metrics and V3-only for unpaired dates.
        rank = (paired_capture(snapshot), retrieval)
        if day not in candidates or rank > candidates[day][0]:
            candidates[day] = rank, value, snapshot.get("forecast_v2_kwh") if rank[0] else None
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
    matches_by_day={}
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
        (_, retrieved), predicted, v2 = candidates[day]
        matches_by_day[day]= {"date_local":day,"forecast_kwh":round(predicted,3),
                        "actual_kwh":round(actual,3),
                        "error_kwh":round(predicted-actual,3),
                        "error_pct":round(100*(predicted-actual)/actual,2) if actual>0 else None,
                        "retrieved_at_utc":retrieved.isoformat(),
                        "actual_source":row["solar_energy_kwh_source"],
                        "actual_method":AUTHORITATIVE_METHOD,
                        "provider_issue_time_verified":False}
        if v2 is not None:
            matches_by_day[day]["v2_kwh"] = v2
    matches=sorted(matches_by_day.values(), key=lambda x:x["date_local"])
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
    paired=[m for m in matches if "v2_kwh" in m]
    result["paired_days"]=len(paired)
    for m in paired:
        a=m["actual_kwh"]
        e2=m["v2_kwh"]-a
        e3=m["forecast_kwh"]-a
        m.update(v2_error_kwh=round(e2,3),v3_error_kwh=round(e3,3),v2_error_pct=round(100*e2/a,2) if a>0 else None,winner="tie" if abs(abs(e2)-abs(e3))<0.0005 else "V2" if abs(e2)<abs(e3) else "V3")
    for model,key in (("v2","v2_error_kwh"),("v3","v3_error_kwh")):
        if paired:
            errors=[m[key] for m in paired]
            result.update({model+"_mae":round(sum(map(abs,errors))/len(errors),3),model+"_rmse":round(sqrt(sum(e*e for e in errors)/len(errors)),3),model+"_bias":round(sum(errors)/len(errors),3)})
    result["paired_history"]=[{k:m[k] for k in ("date_local","actual_kwh","v2_kwh","forecast_kwh","winner")} for m in paired[-30:]]
    return result


def preserve_daily_forecasts(existing, snapshots, timezone_name):
    """Retain the latest paired and latest V3-only captures per local date."""
    archive_fields = (
        "target_date_local", "retrieved_at_utc", "local_timezone",
        "forecast_v2_kwh", "forecast_v3_kwh", "forecast_issue_time_verified",
        "hourly_array_forecasts", "array_power_entities", "array_forecasts",
        "zeus_version", "model_revision", "secondary_weather_hours", "secondary_retrieved_at_utc",
    )
    selected = {}
    for row in [*existing, *snapshots]:
        try:
            day = date.fromisoformat(row["target_date_local"])
            captured = datetime.fromisoformat(row["retrieved_at_utc"])
            capture_zone = row.get("local_timezone")
            zone = ZoneInfo(capture_zone or timezone_name)
            if (captured.utcoffset() is None or captured.astimezone(zone).date() >= day
                    or not valid_forecast(row.get("forecast_v3_kwh"))):
                continue
        except (KeyError, TypeError, ValueError, OverflowError, ZoneInfoNotFoundError):
            continue
        paired = paired_capture(row)
        captures = selected.setdefault(day, {})
        if paired not in captures or captured > captures[paired][0]:
            archived = {field: row.get(field) for field in archive_fields}
            if not paired:
                archived["forecast_v2_kwh"] = None
            captures[paired] = captured, archived
    return [row for day in sorted(selected)[-365:] for _, row in sorted(
        selected[day].values(), key=lambda item: item[0]
    )]
