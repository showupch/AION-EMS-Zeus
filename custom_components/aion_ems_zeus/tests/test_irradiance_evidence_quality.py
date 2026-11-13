from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
_spec = importlib.util.spec_from_file_location("irradiance_evidence_quality", Path(__file__).resolve().parents[1] / "engines" / "irradiance_evidence_quality.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
summarize_paired_samples = _mod.summarize_paired_samples


def test_small_sample_fails_closed():
    now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    r = summarize_paired_samples([(now, 80, 2000)])
    assert r['status'] == 'insufficient_evidence'
    assert r['production_applied'] is False


def test_three_days_produce_descriptive_only():
    start = datetime(2026, 11, 9, 8, tzinfo=timezone.utc)
    rows = [(start + timedelta(days=d, minutes=30*i), 100 + i, 2000 + 10*i)
            for d in range(3) for i in range(8)]
    r = summarize_paired_samples(rows)
    assert r['status'] == 'descriptive_evidence_available'
    assert r['eligible_buckets'] == 24
    assert r['eligible_days'] == 3
    assert r['forecast_recalculated'] is False


def test_single_day_is_not_three_independent_days():
    start = datetime(2026, 11, 9, 8, tzinfo=timezone.utc)
    rows = [(start + timedelta(minutes=30*i), 100, 2000) for i in range(24)]
    assert summarize_paired_samples(rows)['status'] == 'insufficient_evidence'


def test_invalid_values_rejected():
    now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    r = summarize_paired_samples([(now, float('nan'), 1000), (now, 100, -1), (now, 40, 2000)])
    assert r['rejected_samples'] == 3


def test_timezone_aware_required():
    r = summarize_paired_samples([(datetime(2026, 11, 12, 12), 100, 2000)])
    assert r['rejected_samples'] == 1
