import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('radiation', Path(__file__).resolve().parents[1] / 'engines' / 'open_meteo_radiation.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_valid_utc_day():
    result = mod.parse_radiation_hours({'hourly': {'time': ['2026-11-13T12:00+00:00', '2026-11-14T12:00+00:00'], 'shortwave_radiation': [320, 400]}}, '2026-11-13')
    assert len(result) == 1 and result[0]['shortwave_radiation_w_m2'] == 320


def test_naive_utc_api_times():
    rows = mod.parse_radiation_hours({'hourly': {'time': ['2026-11-13T12:00'], 'shortwave_radiation': [320]}}, '2026-11-13')
    assert len(rows) == 1 and rows[0]["time_utc"] == "2026-11-13T12:00:00+00:00"

def test_reject_naive_non_utc_times():
    assert mod.parse_radiation_hours({'hourly': {'time': ['2026-11-13T12:00'], 'shortwave_radiation': [320]}}, '2026-11-13', response_timezone="Europe/Zurich") == []


def test_reject_invalid():
    assert mod.parse_radiation_hours({'hourly': {'time': ['2026-11-13T12:00Z', '2026-11-13T13:00Z'], 'shortwave_radiation': [-1, 9999]}}, '2026-11-13') == []


def test_reject_mismatched_arrays():
    assert mod.parse_radiation_hours({'hourly': {'time': ['2026-11-13T12:00Z'], 'shortwave_radiation': []}}, '2026-11-13') == []
