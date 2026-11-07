"""Read-only retrospective forecast evaluation regression tests."""
from datetime import date, timedelta
from copy import deepcopy
from zoneinfo import ZoneInfo
from custom_components.aion_ems_zeus.knowledge_v2_pkg.forecast_v21_accuracy_audit import evaluate_frozen_accuracy


def sample(day, actual=12, forecast=10, coverage=1):
    return {"target_date": day.isoformat(),
            "forecast_created_at": (day-timedelta(days=1)).isoformat()+"T20:00:00+00:00",
            "predicted": {"solar_kwh": forecast}, "actual": {"solar_kwh": actual},
            "weather_coverage": coverage}


def test_empty_and_legacy_evidence_do_not_claim_accuracy():
    assert evaluate_frozen_accuracy([], ZoneInfo("Europe/Zurich"))["status"] == "insufficient_evidence"
    rows = [sample(date(2026, 10, 1)+timedelta(days=i), coverage=None) for i in range(12)]
    result = evaluate_frozen_accuracy(rows, ZoneInfo("Europe/Zurich"))
    assert result["qualified_days"] == 0
    assert result["evaluated_days"] == 0
    assert result["baseline"]["mae_kwh"] is None


def test_seven_day_threshold_and_read_only():
    rows = [sample(date(2026, 10, 1)+timedelta(days=i)) for i in range(9)]
    original = deepcopy(rows)
    result = evaluate_frozen_accuracy(rows, ZoneInfo("Europe/Zurich"))
    assert result["qualified_days"] == 9
    assert result["baseline"]["mae_kwh"] == 2
    assert result["baseline"]["bias_kwh"] == -2
    assert rows == original
    assert result["production_applied"] is False
    assert result["control_permission"] is False
    assert all(r["training_days_available"] >= 7 for r in result["days"])


def test_future_outcome_cannot_change_past_predictions():
    rows = [sample(date(2026, 10, 1)+timedelta(days=i)) for i in range(12)]
    first = evaluate_frozen_accuracy(rows, ZoneInfo("Europe/Zurich"))
    rows[-1]["actual"]["solar_kwh"] = 5000
    second = evaluate_frozen_accuracy(rows, ZoneInfo("Europe/Zurich"))
    for left, right in zip(first["days"], second["days"]):
        assert left["existing_v21_kwh"] == right["existing_v21_kwh"]
        assert left["independent_v21_kwh"] == right["independent_v21_kwh"]
