"""Read-only Open-Meteo radiation evidence for shadow comparison.

Not a PV forecast. Never used by planning or control.
"""
from datetime import datetime, timezone
from math import isfinite

API_URL = "https://api.open-meteo.com/v1/forecast"


def parse_radiation_hours(payload, target_date, *, response_timezone="UTC"):
    """Parse UTC-aligned hourly shortwave radiation, rejecting malformed data."""
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    if not isinstance(hourly, dict):
        return []
    times, watts = hourly.get("time"), hourly.get("shortwave_radiation")
    if not isinstance(times, list) or not isinstance(watts, list) or len(times) != len(watts):
        return []
    result = []
    for stamp, value in zip(times, watts):
        try:
            dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                if response_timezone != "UTC":
                    return []  # Only the explicitly UTC API response may omit an offset.
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            irradiance = float(value)
            if not isfinite(irradiance) or irradiance < 0 or irradiance > 1500:
                continue
            if dt.date().isoformat() != target_date:
                continue
            result.append({"time_utc": dt.isoformat(), "shortwave_radiation_w_m2": round(irradiance, 2)})
        except (TypeError, ValueError, OverflowError):
            continue
    return result


async def fetch_radiation(hass, target_date):
    """Query Open-Meteo once, with a bounded timeout; no caching or mutations."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from aiohttp import ClientTimeout
    from datetime import date, timedelta
    target = date.fromisoformat(target_date)
    if not date.today() <= target <= date.today() + timedelta(days=7):
        return {"status": "out_of_range", "hours": []}
    session = async_get_clientsession(hass)
    params = {"latitude": hass.config.latitude, "longitude": hass.config.longitude,
              "hourly": "shortwave_radiation", "timezone": "UTC",
              "start_date": target_date, "end_date": (target + timedelta(days=1)).isoformat()}
    try:
        async with session.get(API_URL, params=params, timeout=ClientTimeout(total=12)) as response:
            if response.status != 200:
                return {"status": "provider_error", "hours": [], "http_status": response.status}
            data = await response.json()
    except Exception:
        return {"status": "unavailable", "hours": []}
    rows = parse_radiation_hours(data, target_date, response_timezone="UTC")
    return {"status": "available" if rows else "no_hourly_data", "hours": rows,
            "source": "Open-Meteo hourly shortwave_radiation", "units": "W/m2",
            "note": "Read-only forecast radiation; not PV energy, not site-measured irradiance."}
