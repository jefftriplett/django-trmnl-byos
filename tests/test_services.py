from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone

from django_trmnl_byos.models import Device, PlaylistItem, PluginInstance
from django_trmnl_byos.services import refresh_instance, render_targets, run_once

from .conftest import fake_render

pytestmark = pytest.mark.django_db


def at(hour, minute=0, weekday=0):
    # 2026-09-21 is a Monday.
    return datetime(2026, 9, 21 + weekday, hour, minute)


@pytest.mark.parametrize(
    "start, end, when, active",
    [
        (None, None, at(3), True),
        (time(9), time(17), at(8, 59), False),
        (time(9), time(17), at(9), True),
        (time(9), time(17), at(17), False),
        (time(22), time(6), at(23), True),
        (time(22), time(6), at(5), True),
        (time(22), time(6), at(12), False),
    ],
)
def test_time_windows(start, end, when, active):
    assert PlaylistItem(start_time=start, end_time=end).is_active(when) is active


def test_weekdays():
    weekend = PlaylistItem(weekdays="56")
    assert not weekend.is_active(at(12, weekday=0))
    assert weekend.is_active(at(12, weekday=5))


def test_needs_render(single, message):
    assert single.needs_render("og")
    render = fake_render(single)
    assert not single.needs_render("og")
    assert single.needs_render("og", now=render.created_at + timedelta(minutes=message.refresh_interval))
    message.save()  # new data
    assert single.needs_render("og")


def test_render_targets_and_run_once(device, single, fluid):
    Device.objects.create(mac_address="11:22:33:44:55:66", playlist=device.playlist, profile="x")
    Device.objects.create(mac_address="11:22:33:44:55:77", playlist=device.playlist, enabled=False)
    assert render_targets() == sorted(
        [(single.pk, "og", "landscape"), (fluid.pk, "og", "landscape"), (single.pk, "x", "landscape"), (fluid.pk, "x", "landscape")]
    )

    class FakeRenderer:
        def __init__(self):
            self.calls = []

        def render(self, dashboard, profile, orientation):
            self.calls.append((dashboard.pk, profile))
            return fake_render(dashboard, profile, orientation)

    renderer = FakeRenderer()
    assert run_once(renderer)["rendered"] == 4
    assert run_once(renderer)["rendered"] == 0  # nothing stale
    assert run_once(renderer, force=True)["rendered"] == 4


def test_refresh_instance_records_errors(monkeypatch):
    instance = PluginInstance.objects.create(name="J", plugin="json_api", settings={"url": ""})
    assert instance.refresh_due()
    assert refresh_instance(instance) is False
    instance.refresh_from_db()
    assert "url" in instance.last_error
    assert not instance.refresh_due()

    monkeypatch.setattr("django_trmnl_byos.plugins.builtin.fetch_json", lambda url, headers=None: [1, 2])
    instance.settings = {"url": "https://example.com/data.json"}
    instance.data_refreshed_at = timezone.now() - timedelta(hours=1)
    instance.save()
    assert refresh_instance(instance) is True
    instance.refresh_from_db()
    assert instance.merge_variables == {"data": [1, 2]}
    assert instance.last_error == ""
