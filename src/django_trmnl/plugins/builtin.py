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
    default_settings = {"timezone": "", "hour_format": "12"}

    def get_context(self, instance, size, trmnl):
        context = super().get_context(instance, size, trmnl)
        now = timezone.localtime(timezone=_zone(context["settings"]["timezone"]))
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
    description = "Current conditions and a 5-day forecast from Open-Meteo."
    template_name = "django_trmnl/plugins/weather.html"
    polls = True
    default_settings = {"latitude": 33.749, "longitude": -84.388, "location": "Atlanta", "units": "fahrenheit"}

    def get_context(self, instance, size, trmnl):
        context = super().get_context(instance, size, trmnl)
        # Five days fit a full view or a large screen; smaller cells get three.
        roomy = size == "full" or trmnl.get("device", {}).get("width", 0) > 900
        context["forecast_days"] = ":5" if roomy else ":3"
        return context

    def fetch(self, instance):
        settings = self.settings_for(instance)
        query = urllib.parse.urlencode(
            {
                "latitude": settings["latitude"],
                "longitude": settings["longitude"],
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "temperature_unit": settings["units"],
                "wind_speed_unit": "mph" if settings["units"] == "fahrenheit" else "kmh",
                "timezone": "auto",
                "forecast_days": 5,
            }
        )
        payload = fetch_json(f"https://api.open-meteo.com/v1/forecast?{query}")
        current = payload["current"]
        daily = payload["daily"]
        return {
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


def _zone(name):
    if not name:
        return timezone.get_current_timezone()
    try:
        return zoneinfo.ZoneInfo(name)
    except zoneinfo.ZoneInfoNotFoundError:
        raise PluginError(f"Unknown timezone {name!r}") from None
