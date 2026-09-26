import json

import pytest
from django.test import override_settings

from django_trmnl_byos.models import PluginInstance

pytestmark = pytest.mark.django_db


@pytest.fixture
def instance():
    return PluginInstance.objects.create(
        name="Hook", plugin="markup", merge_variables={"sensor": {"humidity": 40}, "temps": [1, 2]}
    )


def post(client, instance, payload):
    return client.post(
        f"/api/custom_plugins/{instance.uuid}", data=json.dumps(payload), content_type="application/json"
    )


def test_get_returns_merge_variables(client, instance):
    assert client.get(f"/api/custom_plugins/{instance.uuid}").json() == {
        "merge_variables": instance.merge_variables
    }


def test_replace(client, instance):
    post(client, instance, {"merge_variables": {"text": "You can do it!"}})
    instance.refresh_from_db()
    assert instance.merge_variables == {"text": "You can do it!"}


def test_deep_merge(client, instance):
    post(client, instance, {"merge_variables": {"sensor": {"temperature": 42}}, "merge_strategy": "deep_merge"})
    instance.refresh_from_db()
    assert instance.merge_variables["sensor"] == {"humidity": 40, "temperature": 42}


def test_stream_appends_and_trims(client, instance):
    post(client, instance, {"merge_variables": {"temps": [3, 4]}, "merge_strategy": "stream", "stream_limit": 3})
    instance.refresh_from_db()
    assert instance.merge_variables["temps"] == [2, 3, 4]


def test_bad_requests(client, instance):
    url = f"/api/custom_plugins/{instance.uuid}"
    assert client.post(url, data="nope", content_type="application/json").status_code == 400
    assert post(client, instance, {"merge_variables": [1]}).status_code == 400
    assert post(client, instance, {"merge_variables": {}, "merge_strategy": "x"}).status_code == 400
    assert client.get("/api/custom_plugins/00000000-0000-0000-0000-000000000000").status_code == 404


@override_settings(DJANGO_TRMNL_BYOS={"WEBHOOK_MAX_BYTES": 50})
def test_size_limit(client, instance):
    assert post(client, instance, {"merge_variables": {"text": "x" * 100}}).status_code == 413


def test_webhook_marks_dashboards_stale(client, instance):
    from django_trmnl_byos.models import Dashboard, DashboardCell

    from .conftest import fake_render

    dashboard = Dashboard.objects.create(name="D", layout="1x1")
    DashboardCell.objects.create(dashboard=dashboard, instance=instance)
    fake_render(dashboard)
    assert not dashboard.needs_render("og")
    post(client, instance, {"merge_variables": {"text": "new"}})
    assert dashboard.needs_render("og")
