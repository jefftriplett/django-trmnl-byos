import pytest

from django_trmnl_byos.models import PluginInstance
from django_trmnl_byos.plugins import PluginError, registry, render_markup

pytestmark = pytest.mark.django_db

TRMNL = {"size": "full", "dom_id": "cell-1", "device": {}, "plugin": {}}


def test_registry_has_builtins():
    assert {"message", "clock", "month_calendar", "weather", "markup", "json_api"} <= {
        plugin.key for plugin in registry
    }
    with pytest.raises(PluginError):
        registry.get("nope")


def test_markup_plugin_django_engine_uses_merge_variables():
    instance = PluginInstance.objects.create(
        name="M",
        plugin="markup",
        settings={"markup": {"full": "<b>{{ text }}</b> {{ trmnl.dom_id }}", "quadrant": "Q {{ text }}"}},
        merge_variables={"text": "You can do it!"},
    )
    plugin = instance.get_plugin()
    assert plugin.render(instance, "full", TRMNL) == "<b>You can do it!</b> cell-1"
    assert plugin.render(instance, "quadrant", TRMNL) == "Q You can do it!"
    assert plugin.render(instance, "half_vertical", TRMNL).startswith("<b>")  # falls back to full


def test_liquid_engine_with_trmnl_style_templates():
    shared = "{% template greet %}Hello there, {{ name }}.{% endtemplate %}<style>.x{}</style>"
    html = render_markup(
        '{% render "greet", name: who %} {{ price | plus: 1 }}',
        {"who": "General Kenobi", "price": 9},
        engine="liquid",
        shared=shared,
    )
    assert html == "<style>.x{}</style>Hello there, General Kenobi. 10"


def test_clock_and_calendar_render():
    clock = PluginInstance.objects.create(name="C", plugin="clock", settings={"hour_format": "24", "timezone": "UTC"})
    assert 'class="value' in clock.get_plugin().render(clock, "quadrant", TRMNL)
    calendar = PluginInstance.objects.create(name="Cal", plugin="month_calendar")
    html = calendar.get_plugin().render(calendar, "full", TRMNL)
    assert "label--inverted" in html  # today


def test_unknown_timezone_is_a_plugin_error():
    clock = PluginInstance.objects.create(name="C", plugin="clock", settings={"timezone": "Mars/Olympus"})
    with pytest.raises(PluginError):
        clock.get_plugin().render(clock, "full", TRMNL)


def test_weather_fetch_parses_open_meteo(monkeypatch):
    payload = {
        "current": {
            "temperature_2m": 71.6,
            "apparent_temperature": 70.2,
            "weather_code": 2,
            "wind_speed_10m": 5.4,
            "relative_humidity_2m": 40,
        },
        "daily": {
            "time": ["2026-09-26", "2026-09-27"],
            "temperature_2m_max": [80.1, 78],
            "temperature_2m_min": [60, 59.5],
            "weather_code": [0, 61],
        },
    }
    geocode = {
        "results": [
            {"name": "Lawrence", "admin1": "Massachusetts", "latitude": 42.7, "longitude": -71.16},
            {"name": "Lawrence", "admin1": "Kansas", "latitude": 38.97167, "longitude": -95.23525},
        ]
    }
    urls = []

    def fake_fetch(url, headers=None):
        urls.append(url)
        return geocode if "geocoding-api" in url else payload

    monkeypatch.setattr("django_trmnl_byos.plugins.builtin.fetch_json", fake_fetch)
    weather = PluginInstance.objects.create(name="W", plugin="weather", settings={"location": "Lawrence, KS"})
    data = weather.get_plugin().fetch(weather)
    assert data["place"] == {"name": "Lawrence, KS", "latitude": 38.97167, "longitude": -95.23525}
    assert "latitude=38.97167" in urls[-1]
    assert weather.get_plugin().title(PluginInstance(name="Weather", merge_variables=data)) == "Weather · Lawrence, KS"
    assert data["current"]["temperature"] == 72
    assert data["current"]["conditions"] == "Partly cloudy"
    assert data["forecast"][1] == {"date": "2026-09-27", "high": 78, "low": 60, "conditions": "Light rain"}


@pytest.mark.parametrize(
    "name, zone",
    [
        ("America/Chicago", "America/Chicago"),
        ("Chicago", "America/Chicago"),
        ("chicago", "America/Chicago"),
        ("Central", "America/Chicago"),
        ("CST", "America/Chicago"),
        ("central time", "America/Chicago"),
        ("Eastern", "America/New_York"),
        ("new york", "America/New_York"),
        ("UTC", "UTC"),
    ],
)
def test_resolve_timezone(name, zone):
    from django_trmnl_byos.plugins.builtin import resolve_timezone

    assert str(resolve_timezone(name)) == zone


def test_clock_shows_time_in_its_timezone():
    clock = PluginInstance.objects.create(name="C", plugin="clock", settings={"timezone": "Central"})
    html = clock.get_plugin().render(clock, "full", TRMNL)
    assert "CST" in html or "CDT" in html


def test_weather_uses_coordinates_without_geocoding(monkeypatch):
    urls = []
    monkeypatch.setattr(
        "django_trmnl_byos.plugins.builtin.fetch_json",
        lambda url, headers=None: urls.append(url)
        or {
            "current": {"temperature_2m": 50, "apparent_temperature": 48, "weather_code": 3, "wind_speed_10m": 3, "relative_humidity_2m": 60},
            "daily": {"time": [], "temperature_2m_max": [], "temperature_2m_min": [], "weather_code": []},
        },
    )
    weather = PluginInstance.objects.create(
        name="W", plugin="weather", settings={"latitude": 38.97, "longitude": -95.24, "label": "Office", "units": "celsius"}
    )
    data = weather.get_plugin().fetch(weather)
    assert len(urls) == 1 and "geocoding" not in urls[0]
    assert data["place"]["name"] == "Office"
    assert data["units"] == "°C"


@pytest.mark.parametrize(
    "plugin, values, error",
    [
        ("clock", {"timezone": "Mars/Olympus"}, "Unknown timezone"),
        ("clock", {"hour_format": "13"}, "hour_format"),
        ("weather", {"units": "kelvin"}, "units"),
        ("weather", {"forecast_days": 9}, "forecast_days"),
        ("weather", {"location": ""}, "Set a location"),
    ],
)
def test_invalid_settings_are_rejected(plugin, values, error):
    from django.core.exceptions import ValidationError

    instance = PluginInstance(name="X", plugin=plugin, settings=values)
    with pytest.raises(ValidationError, match=error):
        instance.clean()
