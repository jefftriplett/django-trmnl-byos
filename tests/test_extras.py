import datetime
import urllib.error

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from django_trmnl_byos.models import PluginInstance

pytestmark = pytest.mark.django_db

TRMNL = {"size": "full", "dom_id": "cell-1", "device": {"width": 800}, "plugin": {}}


def render(instance, size="full"):
    return instance.get_plugin().render(instance, size, TRMNL)


def refresh(instance):
    instance.merge_variables = instance.get_plugin().fetch(instance)
    instance.save()
    return instance.merge_variables


# --- iCal ----------------------------------------------------------------

def ics(today):
    monday = today - datetime.timedelta(days=today.weekday())
    tomorrow = today + datetime.timedelta(days=1)
    return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:standup
DTSTART;TZID=America/Chicago:{monday:%Y%m%d}T233000
DTEND;TZID=America/Chicago:{monday:%Y%m%d}T234500
RRULE:FREQ=DAILY
SUMMARY:Late standup
LOCATION:Office
END:VEVENT
BEGIN:VEVENT
UID:holiday
DTSTART;VALUE=DATE:{tomorrow:%Y%m%d}
DTEND;VALUE=DATE:{tomorrow + datetime.timedelta(days=1):%Y%m%d}
SUMMARY:Day off
END:VEVENT
END:VCALENDAR
""".encode()


def test_ical_expands_repeating_events(monkeypatch):
    today = timezone.localdate(timezone=datetime.timezone(datetime.timedelta(hours=-5)))
    monkeypatch.setattr("django_trmnl_byos.plugins.extras.fetch_bytes", lambda url, headers=None: ics(today))
    instance = PluginInstance.objects.create(
        name="Cal", plugin="ical", settings={"urls": ["webcal://example.com/cal.ics"], "days": 3, "timezone": "Central"}
    )
    events = refresh(instance)["events"]
    assert [event["title"] for event in events].count("Late standup") >= 3  # daily, over 3 days
    assert any(event["title"] == "Day off" and event["all_day"] for event in events)
    html = render(instance)
    assert "Late standup" in html and "11:30 PM" in html and "Office" in html
    assert "Tomorrow · All day" in html


def test_ical_settings_are_validated():
    with pytest.raises(ValidationError, match="ics link"):
        PluginInstance(name="C", plugin="ical", settings={"urls": []}).clean()
    with pytest.raises(ValidationError, match="webcal"):
        PluginInstance(name="C", plugin="ical", settings={"urls": ["ftp://x"]}).clean()


# --- RSS / Atom ----------------------------------------------------------

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Django News</title>
<item><title>Issue 300</title><link>https://example.com/300</link><pubDate>Fri, 25 Sep 2026 14:00:00 GMT</pubDate></item>
<item><title>Issue 299</title><link>https://example.com/299</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Blog</title>
<entry><title>Hello</title><link href="https://example.com/hello"/><updated>2026-09-20T10:00:00Z</updated></entry>
</feed>"""


@pytest.mark.parametrize("payload, title, first", [(RSS, "Django News", "Issue 300"), (ATOM, "Blog", "Hello")])
def test_feeds(monkeypatch, payload, title, first):
    monkeypatch.setattr("django_trmnl_byos.plugins.extras.fetch_bytes", lambda url, headers=None: payload)
    instance = PluginInstance.objects.create(name="Feed", plugin="rss", settings={"url": "https://example.com/feed"})
    data = refresh(instance)
    assert data["feed_title"] == title
    assert data["items"][0]["title"] == first
    assert first in render(instance)


def test_feed_rejects_non_feeds(monkeypatch):
    from django_trmnl_byos.plugins import PluginError

    monkeypatch.setattr("django_trmnl_byos.plugins.extras.fetch_bytes", lambda url, headers=None: b"<html></html>")
    instance = PluginInstance.objects.create(name="Feed", plugin="rss", settings={"url": "https://example.com/"})
    with pytest.raises(PluginError, match="Not an RSS or Atom feed"):
        instance.get_plugin().fetch(instance)


# --- Countdown -----------------------------------------------------------

def test_countdown_orders_and_labels():
    today = timezone.localdate()
    instance = PluginInstance.objects.create(
        name="Soon",
        plugin="countdown",
        settings={
            "events": [
                {"name": "Far", "date": str(today + datetime.timedelta(days=30))},
                {"name": "Near", "date": str(today + datetime.timedelta(days=3))},
                {"name": "Gone", "date": str(today - datetime.timedelta(days=2))},
            ]
        },
    )
    context = instance.get_plugin().get_context(instance, "full", TRMNL)
    assert [c["name"] for c in context["countdowns"]] == ["Near", "Far"]
    html = render(instance)
    assert "days until Near" in html and "in 30 days" in html and "Gone" not in html


def test_countdown_validates_dates():
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        PluginInstance(name="C", plugin="countdown", settings={"events": [{"name": "X", "date": "soon"}]}).clean()


# --- GitHub --------------------------------------------------------------

def test_github_repos(monkeypatch):
    calls = []

    def fake(url, headers=None):
        calls.append((url, headers.get("Authorization")))
        if url.endswith("/releases/latest"):
            if "beta" in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return {"tag_name": "v1.2.0", "published_at": "2026-09-01T00:00:00Z"}
        return {"full_name": url.split("/repos/")[1], "stargazers_count": 42, "forks_count": 3, "open_issues_count": 5}

    monkeypatch.setattr("django_trmnl_byos.plugins.extras.fetch_json", fake)
    instance = PluginInstance.objects.create(
        name="GH", plugin="github_repos", settings={"repos": ["a/alpha", "b/beta"], "token": "t0k"}
    )
    repos = refresh(instance)["repos"]
    assert repos[0]["release"]["tag"] == "v1.2.0" and repos[1]["release"] is None
    assert all(auth == "Bearer t0k" for _, auth in calls)
    html = render(instance)
    assert "a/alpha" in html and "v1.2.0" in html


def test_github_validates_repo_names():
    with pytest.raises(ValidationError, match="owner/name"):
        PluginInstance(name="G", plugin="github_repos", settings={"repos": ["nope"]}).clean()


# --- Website status ------------------------------------------------------

def test_website_status(monkeypatch):
    def fake(url, headers=None):
        if "down" in url:
            raise urllib.error.URLError("connection refused")
        if "broken" in url:
            raise urllib.error.HTTPError(url, 503, "Unavailable", {}, None)
        return b"ok"

    monkeypatch.setattr("django_trmnl_byos.plugins.extras.fetch_bytes", fake)
    instance = PluginInstance.objects.create(
        name="Status",
        plugin="website_status",
        settings={"sites": ["https://up.example.com/", {"name": "API", "url": "https://down.example.com"}, "https://broken.example.com"]},
    )
    sites = refresh(instance)["sites"]
    assert [site["up"] for site in sites] == [True, False, False]
    assert sites[0]["name"] == "up.example.com" and sites[1]["name"] == "API"
    assert sites[1]["error"] == "connection refused" and sites[2]["error"] == "HTTP 503"
    assert "2 of 3 down" in render(instance)
    assert "2 down" in render(instance, "quadrant")


def test_all_new_plugins_render_empty_in_every_size():
    for key in ("ical", "rss", "countdown", "github_repos", "website_status"):
        instance = PluginInstance.objects.create(name=key, plugin=key)
        for size in ("full", "half_horizontal", "half_vertical", "quadrant"):
            assert 'class="layout' in render(instance, size)


def test_rss_plugin_needs_a_url():
    from django_trmnl_byos.plugins import registry

    plugin = registry.get("rss")
    assert plugin.name == "RSS feed" and plugin.default_settings["url"] == ""
    with pytest.raises(ValidationError, match="feed's URL"):
        PluginInstance(name="RSS feed", plugin="rss", settings={}).clean()


@pytest.mark.parametrize("key, setting", [("github_repos", "repos"), ("website_status", "sites")])
def test_list_plugins_start_empty(key, setting):
    from django_trmnl_byos.plugins import registry

    assert registry.get(key).default_settings[setting] == []
    with pytest.raises(ValidationError):
        PluginInstance(name=key, plugin=key, settings={}).clean()
