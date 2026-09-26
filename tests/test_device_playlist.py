import pytest
from django.urls import reverse

from django_trmnl.models import Dashboard, DashboardCell, Device, Playlist

from .conftest import fake_render

pytestmark = pytest.mark.django_db


@pytest.fixture
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr("django_trmnl.preview.enqueue_render", lambda ids: calls.extend(ids))
    return calls


def act(client, device, **data):
    return client.post(reverse("django_trmnl:preview-device-playlist", args=[device.pk]), data)


def order(playlist):
    return [item.dashboard.name for item in playlist.items.order_by("order", "pk")]


def test_page_shows_controls_and_sharing(admin_client, device, message):
    Device.objects.create(mac_address="11:22:33:44:55:66", name="Kitchen", playlist=device.playlist)
    content = admin_client.get(f"/trmnl/devices/{device.pk}/").content.decode()
    assert "Show next" in content
    assert "Shared with Kitchen" in content
    assert "Add to playlist" not in content  # every dashboard is already in it


def test_show_next_sets_the_next_item(admin_client, device, single, fluid, queued):
    item = device.playlist.items.get(dashboard=fluid)
    response = act(admin_client, device, action="show_next", item=item.pk)
    assert response.status_code == 302
    device.refresh_from_db()
    assert device.playlist_position == 1
    assert queued == [fluid.pk]  # no image yet, so it's queued
    # The device's next /api/display returns it.
    fake_render(single)
    render = fake_render(fluid, data=b"fluid")
    body = admin_client.get("/api/display", HTTP_ACCESS_TOKEN=device.api_key, HTTP_ID=device.mac_address).json()
    assert body["filename"] == render.filename


def test_show_next_includes_an_excluded_item(admin_client, device, fluid, queued):
    item = device.playlist.items.get(dashboard=fluid)
    item.enabled = False
    item.save()
    act(admin_client, device, action="show_next", item=item.pk)
    item.refresh_from_db()
    assert item.enabled


def test_toggle_reorder_remove_and_add(admin_client, device, message, queued):
    playlist = device.playlist
    first = playlist.items.order_by("order").first()
    act(admin_client, device, action="toggle", item=first.pk)
    first.refresh_from_db()
    assert not first.enabled

    assert order(playlist) == ["Single", "Fluid"]
    act(admin_client, device, action="down", item=first.pk)
    assert order(playlist) == ["Fluid", "Single"]
    act(admin_client, device, action="up", item=first.pk)
    assert order(playlist) == ["Single", "Fluid"]

    act(admin_client, device, action="remove", item=first.pk)
    assert order(playlist) == ["Fluid"]

    extra = Dashboard.objects.create(name="Extra", layout="1x1")
    DashboardCell.objects.create(dashboard=extra, instance=message)
    act(admin_client, device, action="add", dashboard=extra.pk)
    assert order(playlist) == ["Fluid", "Extra"]
    assert queued == [extra.pk]


def test_render_queues_the_dashboard(admin_client, device, fluid, queued):
    act(admin_client, device, action="render", item=device.playlist.items.get(dashboard=fluid).pk)
    assert queued == [fluid.pk]


def test_assign_and_copy_playlist(admin_client, device):
    other = Playlist.objects.create(name="Other")
    act(admin_client, device, action="assign", playlist=other.pk)
    device.refresh_from_db()
    assert device.playlist == other

    act(admin_client, device, action="assign", playlist="")
    device.refresh_from_db()
    assert device.playlist is None


def test_copy_gives_the_device_its_own_playlist(admin_client, device):
    shared = device.playlist
    act(admin_client, device, action="create")
    device.refresh_from_db()
    assert device.playlist != shared
    assert order(device.playlist) == order(shared)


def test_items_from_other_playlists_are_refused(admin_client, device, single):
    stranger = Playlist.objects.create(name="Stranger").items.create(dashboard=single)
    assert act(admin_client, device, action="remove", item=stranger.pk).status_code == 404


def test_needs_staff(client, device):
    assert act(client, device, action="render").status_code == 302
    assert Device.objects.get(pk=device.pk).playlist is not None


def test_logs_are_flattened_newest_first(admin_client, device):
    from django_trmnl.models import DeviceLog

    DeviceLog.objects.create(
        device=device,
        message={
            "logs": [
                {"level": "error", "message": "first", "source_path": "src/bl.cpp", "source_line": 7, "wake_reason": "timer"},
                {"level": "error", "message": "second failure", "source_path": "src/api.cpp", "source_line": 9},
            ]
        },
    )
    content = admin_client.get(f"/trmnl/devices/{device.pk}/").content.decode()
    assert "src/bl.cpp:7" in content
    assert content.index("second failure") < content.index("src/bl.cpp:7")  # newest entry first
