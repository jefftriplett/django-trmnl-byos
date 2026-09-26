import json

import pytest
from django_q.models import OrmQ, Schedule

from django_trmnl import tasks
from django_trmnl.models import Device

from .conftest import fake_render

pytestmark = pytest.mark.django_db


class FakeRenderer:
    calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        pass

    def render(self, dashboard, profile, orientation):
        FakeRenderer.calls.append((dashboard.pk, profile, orientation))
        return fake_render(dashboard, profile, orientation)


@pytest.fixture
def fake_renderer(monkeypatch):
    FakeRenderer.calls = []
    monkeypatch.setattr("django_trmnl.rendering.Renderer", FakeRenderer)
    return FakeRenderer


@pytest.fixture
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr("django_q.tasks.async_task", lambda func, *args, **kwargs: calls.append((func, args)))
    return calls


def test_tick_schedule_is_created_on_migrate():
    schedule = Schedule.objects.get(name=tasks.TICK_NAME)
    assert schedule.func == "django_trmnl.tasks.tick"
    assert (schedule.schedule_type, schedule.minutes, schedule.repeats) == (Schedule.MINUTES, 1, -1)


def test_tick_renders_stale_targets(fake_renderer, device, single, fluid):
    assert tasks.tick()["rendered"] == 2
    assert tasks.tick()["rendered"] == 0


def test_render_dashboard_covers_every_profile_showing_it(fake_renderer, device, fluid):
    Device.objects.create(mac_address="11:22:33:44:55:66", playlist=device.playlist, profile="x")
    assert tasks.render_dashboard(fluid.pk) == 2
    assert sorted(fake_renderer.calls) == [(fluid.pk, "og", "landscape"), (fluid.pk, "x", "landscape")]


def test_webhook_queues_a_render(client, queued, single, message):
    client.post(
        f"/api/custom_plugins/{message.uuid}",
        data=json.dumps({"merge_variables": {"message": "hi"}}),
        content_type="application/json",
    )
    assert queued == [("django_trmnl.tasks.render_dashboard", (single.pk,))]


def test_display_without_image_queues_a_render(client, queued, device, single):
    client.get("/api/display", HTTP_ID=device.mac_address, HTTP_ACCESS_TOKEN=device.api_key)
    assert ("django_trmnl.tasks.render_dashboard", (single.pk,)) in queued


def test_enqueue_uses_the_orm_broker(single):
    tasks.enqueue_render([single.pk, single.pk])
    assert OrmQ.objects.count() == 1
