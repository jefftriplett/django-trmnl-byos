import calendar
import urllib.parse
import zoneinfo

from django.utils import timezone

from .base import Plugin, PluginError, fetch_json, registry, render_markup


@registry.register
class MessagePlugin(Plugin):
    key = "message"
    name = "Message"
    description = "A title, a message and an optional author."
    template_name = "django_trmnl/plugins/message.html"
    default_settings = {"title": "", "message": "Hello from django-trmnl.", "author": ""}


@registry.register
class ClockPlugin(Plugin):
    key = "clock"
    name = "Clock"
    description = "The time and date when the screen was rendered."
    template_name = "django_trmnl/plugins/clock.html"
    default_settings = {"timezone": "", "hour_format": "12", "show_timezone": True}
    help = {
        "timezone": 'A zone like "America/Chicago", a city like "Chicago", or "Central"/"CST". Blank uses TIME_ZONE.',
        "hour_format": '"12" or "24".',
        "show_timezone": "Show the zone abbreviation (CDT, EST, …) under the date.",
    }

    def clean_settings(self, settings):
        resolve_timezone(settings.get("timezone", ""))
        if str(settings.get("hour_format", "12")) not in ("12", "24"):
            raise PluginError('hour_format must be "12" or "24".')

    def get_context(self, instance, size, trmnl):
        context = super().get_context(instance, size, trmnl)
        now = timezone.localtime(timezone=resolve_timezone(context["settings"]["timezone"]))
        context["now"] = now
        context["time_format"] = "H:i" if str(context["settings"]["hour_format"]) == "24" else "g:i A"
        return context


@registry.register
class MonthCalendarPlugin(Plugin):
    key = "month_calendar"
    name = "Month calendar"
    description = "This month as a grid, with today highlighted."
    template_name = "django_trmnl/plugins/month_calendar.html"
    default_settings = {"first_weekday": 6, "timezone": ""}

    def get_context(self, instance, size, trmnl):
        context = super().get_context(instance, size, trmnl)
        settings = context["settings"]
        today = timezone.localdate(timezone=_zone(settings["timezone"]))
        month = calendar.Calendar(firstweekday=int(settings["first_weekday"]))
        context["today"] = today
        context["weeks"] = month.monthdayscalendar(today.year, today.month)
        context["day_names"] = [calendar.day_abbr[(int(settings["first_weekday"]) + i) % 7] for i in range(7)]
        return context


WEATHER_CODES = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm, hail",
    99: "Thunderstorm, hail",
}


@registry.register
class WeatherPlugin(Plugin):
    """Current conditions and a short forecast from Open-Meteo (no API key needed)."""

    key = "weather"
    name = "Weather"
    description = "Current conditions and a forecast from Open-Meteo. Set a ZIP code or place name."
    template_name = "django_trmnl/plugins/weather.html"
    polls = True
    default_settings = {
        "location": "66044",
        "country_code": "US",
        "label": "",
        "latitude": None,
        "longitude": None,
        "units": "fahrenheit",
        "forecast_days": 5,
        "show_details": True,
    }
    help = {
        "location": 'ZIP/postal code or place name, e.g. "66044" or "Lawrence, KS".',
        "country_code": 'Two-letter country code to narrow the search (e.g. "US"); blank searches everywhere.',
        "label": "Name to show on screen; blank uses the place found.",
        "latitude": "Set latitude and longitude to skip the location search.",
        "longitude": "Set latitude and longitude to skip the location search.",
        "units": '"fahrenheit" or "celsius".',
        "forecast_days": "Days of forecast to show, 0-7.",
        "show_details": "Show feels-like, humidity and wind.",
    }

    def clean_settings(self, settings):
        if settings.get("units", "fahrenheit") not in ("fahrenheit", "celsius"):
            raise PluginError('units must be "fahrenheit" or "celsius".')
        try:
            days = int(settings.get("forecast_days", 5))
        except (TypeError, ValueError):
            raise PluginError("forecast_days must be a number.") from None
        if not 0 <= days <= 7:
            raise PluginError("forecast_days must be between 0 and 7.")
        has_coordinates = settings.get("latitude") not in (None, "") and settings.get("longitude") not in (None, "")
        if not has_coordinates and not str(settings.get("location", "")).strip():
            raise PluginError("Set a location (ZIP or place name) or latitude and longitude.")

    def get_context(self, instance, size, trmnl):
        context = super().get_context(instance, size, trmnl)
        days = int(context["settings"].get("forecast_days", 5))
        # Small cells on small screens get at most three days.
        roomy = size == "full" or trmnl.get("device", {}).get("width", 0) > 900
        context["forecast_days"] = f":{days if roomy else min(days, 3)}"
        return context

    def title(self, instance):
        place = (instance.merge_variables or {}).get("place", {}).get("name")
        return f"{instance.name} · {place}" if place else instance.name

    def locate(self, settings):
        """Latitude, longitude and a display name, from settings or Open-Meteo geocoding."""
        latitude, longitude = settings.get("latitude"), settings.get("longitude")
        if latitude not in (None, "") and longitude not in (None, ""):
            return float(latitude), float(longitude), settings.get("label") or f"{latitude}, {longitude}"
        query = str(settings.get("location", "")).strip()
        # "Lawrence, KS" → search "Lawrence", then prefer the matching state.
        name, _, region = (part.strip() for part in query.partition(","))
        params = {"name": name, "count": 10, "language": "en", "format": "json"}
        if settings.get("country_code"):
            params["countryCode"] = settings["country_code"]
        results = fetch_json(
            f"https://geocoding-api.open-meteo.com/v1/search?{urllib.parse.urlencode(params)}"
        ).get("results") or []
        if region:
            wanted = {region.lower(), US_STATES.get(region.upper(), "").lower()}
            results = [result for result in results if str(result.get("admin1", "")).lower() in wanted] or results
        if not results:
            raise PluginError(f"Couldn't find a place for {query!r}.")
        place = results[0]
        region_name = place.get("admin1") or place.get("country", "")
        state = STATE_CODES.get(region_name, region_name)
        return place["latitude"], place["longitude"], settings.get("label") or f"{place['name']}, {state}"

    def fetch(self, instance):
        settings = self.settings_for(instance)
        latitude, longitude, place_name = self.locate(settings)
        query = urllib.parse.urlencode(
            {
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "temperature_unit": settings["units"],
                "wind_speed_unit": "mph" if settings["units"] == "fahrenheit" else "kmh",
                "timezone": "auto",
                "forecast_days": max(1, int(settings.get("forecast_days", 5))),
            }
        )
        payload = fetch_json(f"https://api.open-meteo.com/v1/forecast?{query}")
        current = payload["current"]
        daily = payload["daily"]
        return {
            "place": {"name": place_name, "latitude": latitude, "longitude": longitude},
            "current": {
                "temperature": round(current["temperature_2m"]),
                "feels_like": round(current["apparent_temperature"]),
                "humidity": current["relative_humidity_2m"],
                "wind": round(current["wind_speed_10m"]),
                "conditions": WEATHER_CODES.get(current["weather_code"], "—"),
            },
            "forecast": [
                {
                    "date": date,
                    "high": round(high),
                    "low": round(low),
                    "conditions": WEATHER_CODES.get(code, "—"),
                }
                for date, high, low, code in zip(
                    daily["time"],
                    daily["temperature_2m_max"],
                    daily["temperature_2m_min"],
                    daily["weather_code"],
                )
            ],
            "units": "°F" if settings["units"] == "fahrenheit" else "°C",
        }


@registry.register
class MarkupPlugin(Plugin):
    """Your own markup, like a trmnl.com private plugin.

    settings:
      engine: "django" (default) or "liquid"
      markup: one template string, or {"full": ..., "half_horizontal": ..., "half_vertical": ..., "quadrant": ...}
      shared: markup prepended to every size
    Data comes from ``merge_variables`` (set them with the webhook).
    """

    key = "markup"
    name = "Custom markup"
    description = "Your own template, fed by the webhook or by hand."
    default_settings = {"engine": "django", "markup": "", "shared": ""}

    def markup_for(self, settings, size):
        markup = settings.get("markup") or ""
        if isinstance(markup, dict):
            return markup.get(size) or markup.get("full") or ""
        return markup

    def render(self, instance, size, trmnl):
        settings = self.settings_for(instance)
        source = self.markup_for(settings, size)
        if not source.strip():
            source = '<div class="layout layout--col layout--center"><span class="label">Add markup in this instance\'s settings.</span></div>'
        return render_markup(
            source,
            self.get_context(instance, size, trmnl),
            engine=settings.get("engine", "django"),
            shared=settings.get("shared", ""),
        )


@registry.register
class JsonApiPlugin(MarkupPlugin):
    """Poll a JSON URL and render it with your own markup.

    settings add ``url`` and optional ``headers``. A JSON list is exposed as ``data``.
    """

    key = "json_api"
    name = "JSON API (polling)"
    description = "Fetch JSON from a URL on a schedule and render it with your markup."
    polls = True
    default_settings = {**MarkupPlugin.default_settings, "url": "", "headers": {}}

    def fetch(self, instance):
        settings = self.settings_for(instance)
        if not settings.get("url"):
            raise PluginError("Set a url in this instance's settings.")
        payload = fetch_json(settings["url"], settings.get("headers"))
        return payload if isinstance(payload, dict) else {"data": payload}


TIMEZONE_ALIASES = {
    "central": "America/Chicago",
    "ct": "America/Chicago",
    "cst": "America/Chicago",
    "cdt": "America/Chicago",
    "eastern": "America/New_York",
    "et": "America/New_York",
    "est": "America/New_York",
    "edt": "America/New_York",
    "mountain": "America/Denver",
    "mt": "America/Denver",
    "mst": "America/Denver",
    "mdt": "America/Denver",
    "arizona": "America/Phoenix",
    "pacific": "America/Los_Angeles",
    "pt": "America/Los_Angeles",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "alaska": "America/Anchorage",
    "hawaii": "Pacific/Honolulu",
    "utc": "UTC",
    "gmt": "UTC",
}


def resolve_timezone(name):
    """A ZoneInfo from "America/Chicago", "Chicago", "Central", "CST", "central time", …

    Blank uses the project's TIME_ZONE.
    """
    name = str(name or "").strip()
    if not name:
        return timezone.get_current_timezone()
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        pass
    key = name.lower().removesuffix(" timezone").removesuffix(" time zone").removesuffix(" time").strip()
    if key in TIMEZONE_ALIASES:
        return zoneinfo.ZoneInfo(TIMEZONE_ALIASES[key])
    city = key.replace(" ", "_")
    for zone in sorted(zoneinfo.available_timezones()):
        if zone.rsplit("/", 1)[-1].lower() == city:
            return zoneinfo.ZoneInfo(zone)
    raise PluginError(f'Unknown timezone {name!r}. Try "America/Chicago", "Chicago" or "Central".')


def _zone(name):
    return resolve_timezone(name)


US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}  # fmt: skip
STATE_CODES = {name: code for code, name in US_STATES.items()}
