"""Account weather preferences and a small, attributed Open-Meteo cache."""

from __future__ import annotations

from hashlib import sha256
import json
import math
import time
from typing import Annotated
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, Request as FastAPIRequest
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import Principal, current_user, write_user

router = APIRouter(prefix="/api/v1", tags=["weather"])
DEFAULT_CITY = {"name": "\u041a\u0430\u0437\u0430\u043d\u044c", "latitude": 55.78874, "longitude": 49.12214,
                "timezone": "Europe/Moscow"}
CACHE_SECONDS = 900
STALE_SECONDS = 86400
USER_AGENT = "StormOS/0.1 (personal weather display)"

CONDITIONS = {
    0: ("\u2600", "\u042f\u0441\u043d\u043e"), 1: ("\u2600", "\u041c\u0430\u043b\u043e\u043e\u0431\u043b\u0430\u0447\u043d\u043e"),
    2: ("\u26c5", "\u041f\u0435\u0440\u0435\u043c\u0435\u043d\u043d\u0430\u044f \u043e\u0431\u043b\u0430\u0447\u043d\u043e\u0441\u0442\u044c"),
    3: ("\u2601", "\u041e\u0431\u043b\u0430\u0447\u043d\u043e"), 45: ("\u224b", "\u0422\u0443\u043c\u0430\u043d"), 48: ("\u224b", "\u0418\u0437\u043c\u043e\u0440\u043e\u0437\u044c"),
    51: ("\u2602", "\u041c\u043e\u0440\u043e\u0441\u044c"), 53: ("\u2602", "\u041c\u043e\u0440\u043e\u0441\u044c"), 55: ("\u2602", "\u0421\u0438\u043b\u044c\u043d\u0430\u044f \u043c\u043e\u0440\u043e\u0441\u044c"),
    56: ("\u2602", "\u041b\u0435\u0434\u044f\u043d\u0430\u044f \u043c\u043e\u0440\u043e\u0441\u044c"), 57: ("\u2602", "\u041b\u0435\u0434\u044f\u043d\u0430\u044f \u043c\u043e\u0440\u043e\u0441\u044c"),
    61: ("\u2602", "\u041d\u0435\u0431\u043e\u043b\u044c\u0448\u043e\u0439 \u0434\u043e\u0436\u0434\u044c"), 63: ("\u2602", "\u0414\u043e\u0436\u0434\u044c"), 65: ("\u2602", "\u0421\u0438\u043b\u044c\u043d\u044b\u0439 \u0434\u043e\u0436\u0434\u044c"),
    66: ("\u2602", "\u041b\u0435\u0434\u044f\u043d\u043e\u0439 \u0434\u043e\u0436\u0434\u044c"), 67: ("\u2602", "\u0421\u0438\u043b\u044c\u043d\u044b\u0439 \u043b\u0435\u0434\u044f\u043d\u043e\u0439 \u0434\u043e\u0436\u0434\u044c"),
    71: ("\u2744", "\u041d\u0435\u0431\u043e\u043b\u044c\u0448\u043e\u0439 \u0441\u043d\u0435\u0433"), 73: ("\u2744", "\u0421\u043d\u0435\u0433"), 75: ("\u2744", "\u0421\u0438\u043b\u044c\u043d\u044b\u0439 \u0441\u043d\u0435\u0433"), 77: ("\u2744", "\u0421\u043d\u0435\u0436\u043d\u0430\u044f \u043a\u0440\u0443\u043f\u0430"),
    80: ("\u2602", "\u041b\u0438\u0432\u0435\u043d\u044c"), 81: ("\u2602", "\u041b\u0438\u0432\u0435\u043d\u044c"), 82: ("\u2602", "\u0421\u0438\u043b\u044c\u043d\u044b\u0439 \u043b\u0438\u0432\u0435\u043d\u044c"),
    85: ("\u2744", "\u0421\u043d\u0435\u0433\u043e\u043f\u0430\u0434"), 86: ("\u2744", "\u0421\u0438\u043b\u044c\u043d\u044b\u0439 \u0441\u043d\u0435\u0433\u043e\u043f\u0430\u0434"),
    95: ("\u26c8", "\u0413\u0440\u043e\u0437\u0430"), 96: ("\u26c8", "\u0413\u0440\u043e\u0437\u0430 \u0441 \u0433\u0440\u0430\u0434\u043e\u043c"), 99: ("\u26c8", "\u0421\u0438\u043b\u044c\u043d\u0430\u044f \u0433\u0440\u043e\u0437\u0430 \u0441 \u0433\u0440\u0430\u0434\u043e\u043c"),
}


class CityChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str = Field(min_length=1, max_length=80)

    @field_validator("city")
    @classmethod
    def clean_city(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Enter a valid city name")
        return value


def _json_get(url: str, timeout: float) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError("Weather provider returned an error")
        payload = response.read(65537)
    if len(payload) > 65536:
        raise ValueError("Weather provider response is too large")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("Weather provider response is invalid")
    return value


def geocode(city: str, timeout: float) -> dict:
    query = urlencode({"name": city, "count": 5, "language": "ru", "format": "json"})
    try:
        results = _json_get(f"https://geocoding-api.open-meteo.com/v1/search?{query}", timeout).get("results")
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError, UnicodeError) as exc:
        raise HTTPException(503, "City lookup is temporarily unavailable") from exc
    if not isinstance(results, list) or not results:
        raise HTTPException(422, "City was not found")
    # Use the best match. Never persist coordinates until the lookup succeeded.
    row = results[0]
    try:
        name = str(row["name"]).strip()
        latitude, longitude = float(row["latitude"]), float(row["longitude"])
        timezone = str(row["timezone"])
        if (not name or not timezone or not math.isfinite(latitude) or not math.isfinite(longitude)
                or len(name) > 80 or len(timezone) > 80
                or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(503, "City lookup returned invalid data") from exc
    country = str(row.get("country", ""))
    return {"name": name, "latitude": latitude, "longitude": longitude,
            "timezone": timezone, "country": country}


def _cache_key(latitude: float, longitude: float) -> str:
    return sha256(f"{latitude:.5f},{longitude:.5f}".encode("ascii")).hexdigest()


def _preference(connection, user_id: str) -> dict:
    row = connection.execute("""SELECT weather_city, weather_latitude, weather_longitude,
        weather_timezone FROM user_preferences WHERE user_id = ?""", (user_id,)).fetchone()
    if row is None:
        return {"city": DEFAULT_CITY["name"], "latitude": DEFAULT_CITY["latitude"],
                "longitude": DEFAULT_CITY["longitude"], "timezone": DEFAULT_CITY["timezone"]}
    return {"city": row["weather_city"], "latitude": row["weather_latitude"],
            "longitude": row["weather_longitude"], "timezone": row["weather_timezone"]}


@router.get("/preferences/weather")
def get_weather_preference(principal: Annotated[Principal, Depends(current_user)],
                           request: FastAPIRequest):
    with request.app.state.database.connection() as connection:
        value = _preference(connection, principal.user.id)
    return {"city": value["city"], "timezone": value["timezone"]}


@router.patch("/preferences/weather")
def set_weather_preference(payload: CityChange, request: FastAPIRequest,
                           principal: Annotated[Principal, Depends(write_user)]):
    match = geocode(payload.city, request.app.state.settings.weather_timeout_seconds)
    with request.app.state.database.transaction() as connection:
        store_preference(connection, principal.user.id, match)
    return {"city": match["name"], "timezone": match["timezone"], "country": match["country"]}


def store_preference(connection, user_id, match):
    """Shared domain write; callers must authorize the account before invoking."""
    connection.execute("""INSERT INTO user_preferences
            (user_id, weather_city, weather_latitude, weather_longitude, weather_timezone)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET
            weather_city=excluded.weather_city, weather_latitude=excluded.weather_latitude,
            weather_longitude=excluded.weather_longitude, weather_timezone=excluded.weather_timezone,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
            (user_id, match["name"], match["latitude"], match["longitude"], match["timezone"]))


@router.get("/weather")
def current_weather(principal: Annotated[Principal, Depends(current_user)], request: FastAPIRequest):
    settings = request.app.state.settings
    now = int(time.time())
    with request.app.state.database.connection() as connection:
        place = _preference(connection, principal.user.id)
        key = _cache_key(place["latitude"], place["longitude"])
        cached = connection.execute("SELECT * FROM weather_cache WHERE cache_key=?", (key,)).fetchone()
    if cached and now - cached["fetched_at"] < settings.weather_cache_seconds:
        return _weather_response(cached, place, stale=False)
    coords = urlencode({"latitude": place["latitude"], "longitude": place["longitude"],
                        "current": "temperature_2m,weather_code", "timezone": place["timezone"]})
    try:
        data = _json_get(f"https://api.open-meteo.com/v1/forecast?{coords}", settings.weather_timeout_seconds)
        current = data.get("current")
        if not isinstance(current, dict):
            raise ValueError("Missing current conditions")
        temperature = float(current["temperature_2m"])
        code = int(current["weather_code"])
        observed_at = str(current["time"])
        if not math.isfinite(temperature) or code not in CONDITIONS:
            raise ValueError("Invalid current conditions")
        if len(observed_at) > 40:
            raise ValueError("Invalid observation time")
        with request.app.state.database.transaction() as connection:
            connection.execute("""INSERT INTO weather_cache(cache_key, city, temperature, weather_code, observed_at, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET city=excluded.city,
                temperature=excluded.temperature, weather_code=excluded.weather_code,
                observed_at=excluded.observed_at, fetched_at=excluded.fetched_at""",
                (key, place["city"], temperature, code, observed_at, now))
            cached = connection.execute("SELECT * FROM weather_cache WHERE cache_key=?", (key,)).fetchone()
        return _weather_response(cached, place, stale=False, observed_at=observed_at)
    except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeError):
        if cached and 0 <= now - cached["fetched_at"] <= STALE_SECONDS:
            return _weather_response(cached, place, stale=True)
        return {"available": False, "city": place["city"], "attribution": "Open-Meteo \u00b7 GeoNames"}


def _weather_response(row, place: dict, stale: bool, observed_at: str | None = None) -> dict:
    icon, condition = CONDITIONS.get(row["weather_code"], ("", "\u041f\u043e\u0433\u043e\u0434\u0430 \u043d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u0430"))
    return {"available": True, "city": place["city"], "temperature_c": round(row["temperature"]),
            "weather_code": row["weather_code"], "icon": icon, "condition": condition,
            "observed_at": observed_at or row["observed_at"], "updated_at": row["fetched_at"], "stale": stale,
            "attribution": "Open-Meteo \u00b7 GeoNames"}
