import pytest
from django.urls import reverse

from .conftest import fake_render

pytestmark = pytest.mark.django_db


def test_preview_pages(admin_client, fluid, device):
    fake_render(fluid, "x")
    assert b"Fluid" in admin_client.get("/trmnl/").content
    page = admin_client.get(f"/trmnl/dashboards/{fluid.pk}/?profile=x")
    assert page.status_code == 200
    assert b"scale(0.555556)" in page.content
    html = admin_client.get(f"/trmnl/dashboards/{fluid.pk}/html/?profile=x")
    assert b"mashup--3x3" in html.content


def test_preview_needs_staff(client, fluid):
    assert client.get("/trmnl/").status_code == 302


@pytest.mark.parametrize(
    "name", ["plugininstance", "dashboard", "playlist", "device", "render", "devicelog"]
)
def test_admin_pages_load(admin_client, name, fluid, device):
    assert admin_client.get(reverse(f"admin:django_trmnl_{name}_changelist")).status_code == 200


def test_admin_change_pages_load(admin_client, fluid, device, message):
    for url in [
        reverse("admin:django_trmnl_dashboard_change", args=[fluid.pk]),
        reverse("admin:django_trmnl_device_change", args=[device.pk]),
        reverse("admin:django_trmnl_plugininstance_change", args=[message.pk]),
        reverse("admin:django_trmnl_dashboard_add"),
    ]:
        assert admin_client.get(url).status_code == 200


def test_admin_rejects_overlapping_fluid_cells(admin_client, message, clock):
    data = {
        "name": "Clash",
        "layout": "3x3",
        "extra_screen_classes": "",
        "cells-TOTAL_FORMS": "2",
        "cells-INITIAL_FORMS": "0",
        "cells-MIN_NUM_FORMS": "0",
        "cells-MAX_NUM_FORMS": "1000",
    }
    for index, (instance, col) in enumerate([(message, 1), (clock, 2)]):
        data.update(
            {
                f"cells-{index}-instance": instance.pk,
                f"cells-{index}-position": 1,
                f"cells-{index}-col": col,
                f"cells-{index}-row": 1,
                f"cells-{index}-col_span": 2,
                f"cells-{index}-row_span": 1,
                f"cells-{index}-show_title_bar": "on",
            }
        )
    response = admin_client.post(reverse("admin:django_trmnl_dashboard_add"), data)
    assert response.status_code == 200
    assert b"overlaps" in response.content


def test_device_page(admin_client, device, single, fluid):
    from django_trmnl.models import DeviceLog

    render = fake_render(single)
    device.last_render = render
    device.save()
    DeviceLog.objects.create(device=device, message={"log": "hi"})
    page = admin_client.get(f"/trmnl/devices/{device.pk}/")
    assert page.status_code == 200
    content = page.content.decode()
    assert 'location.reload()' in content
    assert f"/api/images/{render.pk}.png" in content
    assert "not rendered yet" in content  # the fluid dashboard
    assert ">Next<" in content
    assert "&#x27;log&#x27;: &#x27;hi&#x27;" in content


def test_preview_pages_use_built_css(admin_client, fluid):
    page = admin_client.get("/trmnl/").content.decode()
    assert "/static/django_trmnl/preview.css" in page


def test_health_and_version(client):
    from django_trmnl import __version__

    assert client.get("/health/").status_code == 200
    assert client.get("/apis/version/").json() == {"version": __version__}
