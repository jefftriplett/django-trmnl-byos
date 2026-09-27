import pytest
from django.urls import reverse

from django_trmnl_byos.models import Dashboard, PluginInstance

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    queued = []
    monkeypatch.setattr("django_trmnl_byos.editor.enqueue_render", lambda ids: queued.extend(ids))
    return queued


def cell_data(cells, total=9):
    """POST data for the cells formset: `cells` is a list of dicts for the first rows."""
    data = {
        "cells-TOTAL_FORMS": str(total),
        "cells-INITIAL_FORMS": "0",
        "cells-MIN_NUM_FORMS": "0",
        "cells-MAX_NUM_FORMS": "9",
    }
    for index in range(total):
        cell = {"position": 1, "col": 1, "row": 1, "col_span": 1, "row_span": 1, "show_title_bar": "on", "instance": ""}
        cell.update(cells[index] if index < len(cells) else {})
        for key, value in cell.items():
            if key == "show_title_bar" and not value:
                continue
            data[f"cells-{index}-{key}"] = value
    return data


def test_pages_load(admin_client, single, message):
    for url in [
        reverse("django_trmnl_byos:editor-dashboard-new"),
        reverse("django_trmnl_byos:editor-dashboard", args=[single.pk]),
        reverse("django_trmnl_byos:editor-dashboard-delete", args=[single.pk]),
        reverse("django_trmnl_byos:editor-plugins"),
        reverse("django_trmnl_byos:editor-plugin-choose"),
        reverse("django_trmnl_byos:editor-plugin-new", args=["weather"]),
        reverse("django_trmnl_byos:editor-plugin", args=[message.pk]),
        reverse("django_trmnl_byos:editor-plugin-delete", args=[message.pk]),
    ]:
        assert admin_client.get(url).status_code == 200, url


def test_editor_needs_staff(client):
    assert client.get(reverse("django_trmnl_byos:editor-plugins")).status_code == 302


def test_create_fixed_dashboard(admin_client, message, clock, no_queue):
    data = {"name": "Split", "layout": "1Lx1R", **cell_data([{"instance": message.pk, "position": 1}, {"instance": clock.pk, "position": 2}])}
    response = admin_client.post(reverse("django_trmnl_byos:editor-dashboard-new"), data)
    assert response.status_code == 302
    dashboard = Dashboard.objects.get(name="Split")
    assert [(cell.instance.name, cell.position) for cell in dashboard.ordered_cells()] == [("Note", 1), ("Clock", 2)]
    assert no_queue == [dashboard.pk]


def test_fixed_layout_slot_errors(admin_client, message, clock):
    data = {"name": "Bad", "layout": "1Lx1R", **cell_data([{"instance": message.pk, "position": 1}, {"instance": clock.pk, "position": 1}])}
    response = admin_client.post(reverse("django_trmnl_byos:editor-dashboard-new"), data)
    assert response.status_code == 200
    assert b"Slot 1 is already used" in response.content
    data = {"name": "Bad", "layout": "1x1", **cell_data([{"instance": message.pk, "position": 3}])}
    assert b"slots 1 to 1" in admin_client.post(reverse("django_trmnl_byos:editor-dashboard-new"), data).content
    assert not Dashboard.objects.filter(name="Bad").exists()


def test_create_fluid_dashboard_and_reject_overlaps(admin_client, message, clock):
    good = {
        "name": "Fluid",
        "layout": "3x3",
        **cell_data(
            [
                {"instance": message.pk, "col": 1, "row": 1, "col_span": 2, "row_span": 3},
                {"instance": clock.pk, "col": 3, "row": 1, "col_span": 1, "row_span": 1},
            ]
        ),
    }
    assert admin_client.post(reverse("django_trmnl_byos:editor-dashboard-new"), good).status_code == 302
    placements = [(c.col, c.row, c.col_span, c.row_span) for c in Dashboard.objects.get(name="Fluid").ordered_cells()]
    assert sorted(placements) == [(1, 1, 2, 3), (3, 1, 1, 1)]

    bad = {**good, "name": "Clash", **cell_data([{"instance": message.pk, "col_span": 2}, {"instance": clock.pk, "col": 2}])}
    response = admin_client.post(reverse("django_trmnl_byos:editor-dashboard-new"), bad)
    assert response.status_code == 200 and b"overlaps" in response.content

    off_grid = {**good, "name": "Off", **cell_data([{"instance": message.pk, "col": 3, "col_span": 2}])}
    assert b"runs past the 3x3 grid" in admin_client.post(reverse("django_trmnl_byos:editor-dashboard-new"), off_grid).content


def test_edit_dashboard_removes_cleared_cells(admin_client, fluid, message):
    cells = list(fluid.ordered_cells())
    data = {"name": "Fluid", "layout": "3x3", **cell_data([], total=0)}
    data.update({"cells-TOTAL_FORMS": str(len(cells)), "cells-INITIAL_FORMS": str(len(cells))})
    for index, cell in enumerate(cells):
        data.update(
            {
                f"cells-{index}-id": cell.pk,
                f"cells-{index}-dashboard": fluid.pk,
                f"cells-{index}-instance": cell.instance_id if index == 0 else "",  # clear the second one
                f"cells-{index}-position": cell.position,
                f"cells-{index}-col": cell.col,
                f"cells-{index}-row": cell.row,
                f"cells-{index}-col_span": cell.col_span,
                f"cells-{index}-row_span": cell.row_span,
            }
        )
    response = admin_client.post(reverse("django_trmnl_byos:editor-dashboard", args=[fluid.pk]), data)
    assert response.status_code == 302
    assert fluid.cells.count() == 1


def test_delete_dashboard(admin_client, single):
    assert admin_client.post(reverse("django_trmnl_byos:editor-dashboard-delete", args=[single.pk])).status_code == 302
    assert not Dashboard.objects.filter(pk=single.pk).exists()


def test_create_weather_plugin_with_typed_settings(admin_client):
    data = {
        "name": "Home weather",
        "refresh_interval": 30,
        "settings-location": "66044",
        "settings-country_code": "US",
        "settings-label": "",
        "settings-latitude": "",
        "settings-longitude": "",
        "settings-units": "celsius",
        "settings-forecast_days": "3",
        # show_details unchecked → False
    }
    response = admin_client.post(reverse("django_trmnl_byos:editor-plugin-new", args=["weather"]), data)
    assert response.status_code == 302
    instance = PluginInstance.objects.get(name="Home weather")
    assert instance.plugin == "weather"
    assert instance.settings["units"] == "celsius"
    assert instance.settings["forecast_days"] == 3
    assert instance.settings["show_details"] is False
    assert instance.settings["latitude"] is None


def test_plugin_settings_errors_are_shown(admin_client):
    data = {"name": "W", "refresh_interval": 15, "settings-location": "66044", "settings-units": "kelvin", "settings-forecast_days": "5"}
    response = admin_client.post(reverse("django_trmnl_byos:editor-plugin-new", args=["weather"]), data)
    assert response.status_code == 200
    assert b"Select a valid choice" in response.content
    assert not PluginInstance.objects.filter(name="W").exists()


def test_list_settings_use_json(admin_client):
    data = {"name": "Repos", "refresh_interval": 15, "settings-repos": '["django/django", "jefftriplett/django-trmnl-byos"]', "settings-token": ""}
    assert admin_client.post(reverse("django_trmnl_byos:editor-plugin-new", args=["github_repos"]), data).status_code == 302
    assert PluginInstance.objects.get(name="Repos").settings["repos"] == ["django/django", "jefftriplett/django-trmnl-byos"]


def test_edit_markup_plugin_data(admin_client, no_queue, single, message):
    instance = PluginInstance.objects.create(name="Mine", plugin="markup", settings={"markup": "<p>{{ text }}</p>"})
    single.cells.create(instance=instance, position=1)
    data = {
        "name": "Mine",
        "refresh_interval": 15,
        "settings-engine": "django",
        "settings-markup": "<p>{{ text }}!</p>",
        "settings-shared": "",
        "data-merge_variables": '{"text": "hello"}',
    }
    assert admin_client.post(reverse("django_trmnl_byos:editor-plugin", args=[instance.pk]), data).status_code == 302
    instance.refresh_from_db()
    assert instance.settings["markup"] == "<p>{{ text }}!</p>"
    assert instance.merge_variables == {"text": "hello"}
    assert single.pk in no_queue


def test_unknown_plugin_type_redirects(admin_client):
    response = admin_client.get(reverse("django_trmnl_byos:editor-plugin-new", args=["nope"]))
    assert response.status_code == 302


def test_refresh_and_delete_plugin(admin_client, monkeypatch, message):
    weather = PluginInstance.objects.create(name="W", plugin="weather")
    monkeypatch.setattr("django_trmnl_byos.editor.refresh_instance", lambda instance: True)
    assert admin_client.post(reverse("django_trmnl_byos:editor-plugin-refresh", args=[weather.pk])).status_code == 302
    assert admin_client.post(reverse("django_trmnl_byos:editor-plugin-delete", args=[message.pk])).status_code == 302
    assert not PluginInstance.objects.filter(pk=message.pk).exists()


def test_limited_settings_are_dropdowns(admin_client):
    content = admin_client.get(reverse("django_trmnl_byos:editor-plugin-new", args=["weather"])).content.decode()
    assert '<select name="settings-units"' in content and '<option value="celsius"' in content


def test_empty_list_setting_shows_an_error_not_a_crash(admin_client):
    data = {"name": "Repos", "refresh_interval": 15, "settings-repos": "", "settings-token": ""}
    response = admin_client.post(reverse("django_trmnl_byos:editor-plugin-new", args=["github_repos"]), data)
    assert response.status_code == 200
    assert b"Add at least one repository" in response.content
    assert not PluginInstance.objects.filter(name="Repos").exists()
