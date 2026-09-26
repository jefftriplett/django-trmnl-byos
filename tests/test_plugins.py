import pytest

from django_trmnl.models import PluginInstance
from django_trmnl.plugins import PluginError, registry, render_markup

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
    monkeypatch.setattr("django_trmnl.plugins.builtin.fetch_json", lambda url, headers=None: payload)
    weather = PluginInstance.objects.create(name="W", plugin="weather")
    data = weather.get_plugin().fetch(weather)
    assert data["current"]["temperature"] == 72
    assert data["current"]["conditions"] == "Partly cloudy"
    assert data["forecast"][1] == {"date": "2026-09-27", "high": 78, "low": 60, "conditions": "Light rain"}
