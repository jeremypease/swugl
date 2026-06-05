"""Apple WeatherKit forecast for events.

Requires three env vars in addition to the existing Apple credentials:
  WEATHERKIT_KEY_ID     — key ID of the WeatherKit key (Apple Developer → Keys)
  WEATHERKIT_SERVICE_ID — bundle ID of the WeatherKit service (e.g. com.swugl.weatherkit)
  APPLE_TEAM_ID         — already set for Sign-In with Apple
  APPLE_PRIVATE_KEY     — already set for Sign-In with Apple (same key can be reused,
                          or a new WeatherKit-specific key can be uploaded)

Geocoding uses the free OpenStreetMap Nominatim API (no key required).
"""
import time
import requests
import jwt as pyjwt
from datetime import date, timedelta
from flask import current_app

_CONDITION_EMOJI = {
    'Clear': '☀️',
    'MostlyClear': '🌤️',
    'PartlyCloudy': '⛅',
    'MostlyCloudy': '🌥️',
    'Cloudy': '☁️',
    'Overcast': '☁️',
    'Fog': '🌫️',
    'FreezingFog': '🌫️',
    'Haze': '🌫️',
    'SmokyHaze': '🌫️',
    'Drizzle': '🌦️',
    'LightRain': '🌦️',
    'SunShowers': '🌦️',
    'Rain': '🌧️',
    'HeavyRain': '🌧️',
    'FreezingDrizzle': '🌨️',
    'FreezingRain': '🌨️',
    'Sleet': '🌨️',
    'WintryMix': '🌨️',
    'Hail': '🌨️',
    'LightSnow': '❄️',
    'Snow': '❄️',
    'HeavySnow': '❄️',
    'Blizzard': '❄️',
    'BlowingSnow': '❄️',
    'ScatteredThunderstorms': '⛈️',
    'IsolatedThunderstorms': '⛈️',
    'Thunderstorms': '⛈️',
    'StrongStorms': '⛈️',
    'TropicalStorm': '🌀',
    'Hurricane': '🌀',
    'Breezy': '💨',
    'Windy': '💨',
    'BlowingDust': '🌪️',
    'Hot': '🌡️',
}

# In-process geocode cache: location string → (timestamp, (lat, lon) | None)
# Entries expire after 24 h; cache is capped at 500 entries to prevent unbounded growth.
_geocode_cache: dict = {}
_GEOCODE_TTL = 86400   # seconds
_GEOCODE_MAX = 500


def _c_to_f(c: float) -> int:
    return round(c * 9 / 5 + 32)


def _weatherkit_jwt() -> str:
    app = current_app._get_current_object()
    team_id = app.config.get('APPLE_TEAM_ID', '')
    key_id = app.config.get('WEATHERKIT_KEY_ID', '')
    service_id = app.config.get('WEATHERKIT_SERVICE_ID', '')
    private_key = (
        app.config.get('WEATHERKIT_PRIVATE_KEY') or
        app.config.get('APPLE_PRIVATE_KEY', '')
    ).replace('\\n', '\n')

    if not all([team_id, key_id, service_id, private_key]):
        return ''

    now = int(time.time())
    return pyjwt.encode(
        {'iss': team_id, 'sub': service_id, 'iat': now, 'exp': now + 60},
        private_key,
        algorithm='ES256',
        headers={'kid': key_id, 'id': f'{team_id}.{service_id}'},
    )


def _geocode(location: str):
    """Return (lat, lon) for a location string, or None on failure."""
    now = time.time()
    entry = _geocode_cache.get(location)
    if entry and now - entry[0] < _GEOCODE_TTL:
        return entry[1]

    try:
        resp = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': location, 'format': 'json', 'limit': 1},
            headers={'User-Agent': 'Swugl/1.0 (support@swugl.com)'},
            timeout=3,
        )
        data = resp.json()
        if data:
            result = (float(data[0]['lat']), float(data[0]['lon']))
        else:
            result = None
    except Exception:
        result = None

    if len(_geocode_cache) >= _GEOCODE_MAX:
        # Evict the oldest 10% of entries to make room
        oldest = sorted(_geocode_cache, key=lambda k: _geocode_cache[k][0])
        for k in oldest[:_GEOCODE_MAX // 10]:
            del _geocode_cache[k]

    _geocode_cache[location] = (now, result)
    return result


def get_event_weather(event) -> list | None:
    """Return a list of daily forecast dicts for the event dates, or None.

    Each dict has: date, emoji, condition, high_f, low_f, rain_chance (0-100).
    Only returns data for events within the next 10 days that have a location.
    """
    if not event.location:
        return None

    today = date.today()
    start = event.start_date
    end = event.end_date or start

    # Only forecast available for the next 10 days
    if start > today + timedelta(days=10) or end < today:
        return None

    coords = _geocode(event.location)
    if not coords:
        return None

    token = _weatherkit_jwt()
    if not token:
        return None

    lat, lon = coords
    try:
        resp = requests.get(
            f'https://weatherkit.apple.com/api/v1/weather/en/{lat}/{lon}',
            params={'dataSets': 'forecastDaily', 'timezone': 'UTC'},
            headers={'Authorization': f'Bearer {token}'},
            timeout=4,
        )
        if resp.status_code != 200:
            return None
        raw_days = resp.json().get('forecastDaily', {}).get('days', [])
    except Exception:
        return None

    # Build a date → forecast dict from the API response
    forecast_by_date = {}
    for d in raw_days:
        try:
            day_date = date.fromisoformat(d['forecastStart'][:10])
            forecast_by_date[day_date] = d
        except (KeyError, ValueError):
            continue

    # Collect the event days that fall within the forecast window
    days_to_show = []
    cursor = max(start, today)
    while cursor <= min(end, today + timedelta(days=10)):
        days_to_show.append(cursor)
        cursor += timedelta(days=1)

    if not days_to_show:
        return None

    result = []
    for day_date in days_to_show:
        d = forecast_by_date.get(day_date)
        if not d:
            continue
        condition = d.get('conditionCode', '')
        result.append({
            'date': day_date,
            'emoji': _CONDITION_EMOJI.get(condition, '🌡️'),
            'condition': condition.replace('MostlyClear', 'Mostly Clear')
                                  .replace('PartlyCloudy', 'Partly Cloudy')
                                  .replace('MostlyCloudy', 'Mostly Cloudy')
                                  .replace('LightRain', 'Light Rain')
                                  .replace('HeavyRain', 'Heavy Rain')
                                  .replace('LightSnow', 'Light Snow')
                                  .replace('HeavySnow', 'Heavy Snow'),
            'high_f': _c_to_f(d.get('temperatureMax', 0)),
            'low_f': _c_to_f(d.get('temperatureMin', 0)),
            'rain_chance': round((d.get('precipitationChance', 0)) * 100),
        })

    return result if result else None
