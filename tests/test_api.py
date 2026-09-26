import json

import pytest
from django.test import override_settings

from django_trmnl.models import Device, DeviceLog, Playlist

from .conftest import fake_render

pytestmark = pytest.mark.django_db


def display(client, device, **headers):
    return client.get(
        "/api/display", HTTP_ID=device.mac_address, HTTP_ACCESS_TOKEN=device.api_key, **headers
    ).json()


def test_setup_provisions_unknown_devices(client, playlist):
    playlist.is_default = True
    playlist.save()
    body = client.get("/api/setup", HTTP_ID="aa-bb-cc-00-11-22", HTTP_MODEL="v2").json()
    device = Device.objects.get()
    assert body["status"] == 200
    assert body["api_key"] == device.api_key
    assert body["friendly_id"] == device.friendly_id
    assert device.mac_address == "AA:BB:CC:00:11:22"
    assert device.profile == "x"
    assert device.playlist == playlist
    # Asking again (with a trailing slash) returns the same credentials.
    again = client.get("/api/setup/", HTTP_ID="AA:BB:CC:00:11:22").json()
    assert again["api_key"] == device.api_key


def test_setup_requires_id(client):
    assert client.get("/api/setup").json()["status"] == 404


@override_settings(DJANGO_TRMNL={"AUTO_PROVISION": False})
def test_setup_without_auto_provision(client):
    assert client.get("/api/setup", HTTP_ID="AA:BB:CC:00:11:22").json()["status"] == 404
    assert not Device.objects.exists()


def test_display_rejects_bad_credentials_without_resetting(client, device):
    # Status 500 would make the firmware wipe itself (including its server URL).
    assert client.get("/api/display").json()["status"] == 202
    assert client.get("/api/display", HTTP_ACCESS_TOKEN="nope").json()["status"] == 202
    wrong_mac = client.get("/api/display", HTTP_ACCESS_TOKEN=device.api_key, HTTP_ID="00:00:00:00:00:00")
    assert wrong_mac.json()["status"] == 202
    assert wrong_mac.json()["reset_firmware"] is False


def test_display_rotates_playlist_and_records_telemetry(client, device, single, fluid):
    first = fake_render(single, data=b"one")
    second = fake_render(fluid, data=b"two")
    bodies = [
        display(client, device, HTTP_BATTERY_VOLTAGE="4.1", HTTP_RSSI="-69", HTTP_FW_VERSION="1.6.2")
        for _ in range(3)
    ]
    assert [body["filename"] for body in bodies] == [first.filename, second.filename, first.filename]
    assert bodies[0]["image_url"] == f"http://testserver/api/images/{first.pk}.png"
    assert [body["refresh_rate"] for body in bodies] == [900, 300, 900]
    assert bodies[0]["status"] == 0
    device.refresh_from_db()
    assert (device.battery_voltage, device.rssi, device.firmware_version) == (4.1, -69, "1.6.2")
    assert device.last_seen_at is not None
    assert device.last_render == first


def test_display_skips_items_without_a_render(client, device, fluid):
    render = fake_render(fluid)
    assert display(client, device)["filename"] == render.filename
    assert display(client, device)["filename"] == render.filename


def test_display_placeholders(client, device):
    assert display(client, device)["filename"] == "placeholder-rendering-og"
    device.playlist = None
    device.save()
    assert display(client, device)["filename"] == "placeholder-welcome-og"
    device.playlist = Playlist.objects.create(name="Empty")
    device.save()
    assert display(client, device)["filename"] == "placeholder-empty-og"
    device.enabled = False
    device.save()
    body = display(client, device)
    assert body["filename"] == "placeholder-disabled-og"
    image = client.get(body["image_url"].replace("http://testserver", ""))
    assert image.status_code == 200
    assert image["Content-Type"] == "image/png"


@override_settings(DJANGO_TRMNL={"BASE_URL": "http://trmnl.lan:8000/"})
def test_base_url_setting(client, device, single):
    render = fake_render(single)
    assert display(client, device)["image_url"] == f"http://trmnl.lan:8000/api/images/{render.pk}.png"


def test_image_endpoint(client, single):
    render = fake_render(single, data=b"BMdata")
    response = client.get(f"/api/images/{render.pk}.png")
    assert response.status_code == 200
    assert response.content == b"BMdata"
    assert response["Content-Type"] == "image/png"
    assert client.get(f"/api/images/{render.pk}.bmp").status_code == 404


def test_log(client, device):
    response = client.post(
        "/api/log",
        data=json.dumps({"log": {"logs_array": [{"message": "oops"}]}}),
        content_type="application/json",
        HTTP_ACCESS_TOKEN=device.api_key,
    )
    assert response.json()["status"] == 200
    assert DeviceLog.objects.get().message["log"]["logs_array"][0]["message"] == "oops"
    client.post("/api/log/", data="not json", content_type="text/plain", HTTP_ACCESS_TOKEN=device.api_key)
    assert DeviceLog.objects.first().message == {"raw": "not json"}
    assert client.post("/api/log", HTTP_ACCESS_TOKEN="nope").status_code == 404


def test_display_adopts_device_registered_elsewhere(client, playlist):
    playlist.is_default = True
    playlist.save()
    body = client.get("/api/display", HTTP_ID="12:34:56:78:9a:bc", HTTP_ACCESS_TOKEN="token-from-trmnl-com").json()
    assert body["status"] == 0
    device = Device.objects.get()
    assert device.api_key == "token-from-trmnl-com"
    assert device.mac_address == "12:34:56:78:9A:BC"
    assert device.playlist == playlist


def test_display_rekeys_known_mac_that_never_checked_in(client, device, single):
    fake_render(single)
    body = client.get("/api/display", HTTP_ID=device.mac_address, HTTP_ACCESS_TOKEN="key-it-kept").json()
    assert body["status"] == 0
    device.refresh_from_db()
    assert device.api_key == "key-it-kept"
    assert Device.objects.count() == 1


def test_display_refuses_new_token_once_device_has_checked_in(client, device, single):
    fake_render(single)
    display(client, device)
    body = client.get("/api/display", HTTP_ID=device.mac_address, HTTP_ACCESS_TOKEN="someone-else").json()
    assert body["status"] == 202
    device.refresh_from_db()
    assert device.api_key != "someone-else"


@override_settings(DJANGO_TRMNL={"AUTO_PROVISION": False})
def test_display_does_not_adopt_without_auto_provision(client):
    body = client.get("/api/display", HTTP_ID="12:34:56:78:9A:BC", HTTP_ACCESS_TOKEN="abc").json()
    assert body["status"] == 202
    assert not Device.objects.exists()



def test_refresh_rate_is_always_an_integer(client, device, single):
    """The firmware and Go clients decode refresh_rate as a number; a string breaks them."""
    unknown = client.get("/api/display", HTTP_ACCESS_TOKEN="nope", HTTP_ID="00:00:00:00:00:01").json()
    rendering = display(client, device)  # no image yet → placeholder
    fake_render(single)
    shown = display(client, device)
    for body in (unknown, rendering, shown):
        assert isinstance(body["refresh_rate"], int), body


def test_current_screen_does_not_advance(client, device, single, fluid):
    fake_render(single, data=b"one")
    fake_render(fluid, data=b"two")
    shown = display(client, device)
    device.refresh_from_db()
    seen_at, position = device.last_seen_at, device.playlist_position
    for path in ("/api/current_screen", "/api/display/current/"):
        body = client.get(path, HTTP_ACCESS_TOKEN=device.api_key).json()
        assert body["status"] == 200
        assert body["image_url"] == shown["image_url"]
        assert body["filename"] == shown["filename"]
        assert isinstance(body["refresh_rate"], int)
        assert body["rendered_at"]
    device.refresh_from_db()
    assert (device.last_seen_at, device.playlist_position) == (seen_at, position)


def test_current_screen_before_anything_is_shown(client, device):
    body = client.get("/api/current_screen", HTTP_ACCESS_TOKEN=device.api_key).json()
    assert body["status"] == 200
    assert body["image_url"].endswith("/rendering.png")
    assert body["filename"] is None and body["rendered_at"] is None


def test_current_screen_unknown_token(client):
    assert client.get("/api/current_screen", HTTP_ACCESS_TOKEN="nope").json()["status"] == 404
    assert client.get("/api/current_screen").json()["status"] == 404
