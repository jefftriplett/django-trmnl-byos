import logging

import pytest

from django_trmnl_byos.models import Dashboard, DashboardCell, Device, Playlist, PlaylistItem, PluginInstance

logging.disable(logging.CRITICAL)


@pytest.fixture(autouse=True)
def use_test_settings(settings):
    settings.DEBUG = False
    settings.MIDDLEWARE = [
        middleware
        for middleware in settings.MIDDLEWARE
        if middleware
        not in {
            "whitenoise.middleware.WhiteNoiseMiddleware",
            "django_browser_reload.middleware.BrowserReloadMiddleware",
        }
    ]
    # Use a faster password hasher
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


@pytest.fixture
def message():
    return PluginInstance.objects.create(
        name="Note", plugin="message", settings={"title": "Hi", "message": "Hello <world>"}
    )


@pytest.fixture
def clock():
    return PluginInstance.objects.create(name="Clock", plugin="clock")


@pytest.fixture
def single(message):
    dashboard = Dashboard.objects.create(name="Single", layout="1x1")
    DashboardCell.objects.create(dashboard=dashboard, instance=message, position=1)
    return dashboard


@pytest.fixture
def fluid(message, clock):
    dashboard = Dashboard.objects.create(name="Fluid", layout="3x3")
    DashboardCell.objects.create(dashboard=dashboard, instance=message, col=1, row=1, col_span=2, row_span=3)
    DashboardCell.objects.create(dashboard=dashboard, instance=clock, col=3, row=1)
    return dashboard


@pytest.fixture
def playlist(single, fluid):
    playlist = Playlist.objects.create(name="Main")
    PlaylistItem.objects.create(playlist=playlist, dashboard=single, order=0)
    PlaylistItem.objects.create(playlist=playlist, dashboard=fluid, order=1, refresh_rate=300)
    return playlist


@pytest.fixture
def device(playlist):
    return Device.objects.create(mac_address="AA:BB:CC:DD:EE:FF", playlist=playlist)


def fake_render(dashboard, profile="og", orientation="landscape", data=b"image"):
    from django_trmnl_byos.models import Render

    return Render.objects.create(
        dashboard=dashboard,
        profile=profile,
        orientation=orientation,
        image=data,
        fingerprint=Render.fingerprint_for(data),
    )
