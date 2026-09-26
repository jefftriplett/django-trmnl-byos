"""Settings for django-trmnl, read from ``settings.DJANGO_TRMNL`` (a dict)."""

from django.conf import settings

FRAMEWORK_VERSION = "3.3.2"

DEFAULTS = {
    # Pinned TRMNL Framework assets. ``latest`` moves; a pinned version renders the same tomorrow.
    "FRAMEWORK_CSS_URL": f"https://trmnl.com/css/{FRAMEWORK_VERSION}/plugins.min.css",
    "FRAMEWORK_JS_URL": f"https://trmnl.com/js/{FRAMEWORK_VERSION}/plugins.min.js",
    # Create a Device the first time an unknown MAC address calls /api/setup.
    "AUTO_PROVISION": True,
    # Profile given to auto-provisioned devices (see django_trmnl.devices.PROFILES).
    "DEFAULT_PROFILE": "og",
    # Seconds between device wake-ups when neither the device nor the playlist item sets one.
    "DEFAULT_REFRESH_RATE": 900,
    # Connect to a remote browser (``playwright run-server``) instead of launching Chromium locally.
    "PLAYWRIGHT_WS_ENDPOINT": None,
    # Milliseconds to wait for the framework runtime (window.TRMNL_PLUGINS_READY) before capturing.
    "RENDER_TIMEOUT": 20_000,
    # Render inside /api/display when a dashboard has no image yet. Handy in development;
    # in production run ``manage.py qcluster`` instead.
    "RENDER_INLINE": False,
    # Largest webhook payload accepted, in bytes. (trmnl.com allows 2 KB; self-hosted can afford more.)
    "WEBHOOK_MAX_BYTES": 64 * 1024,
    # Seconds allowed for a polling plugin's HTTP request.
    "HTTP_TIMEOUT": 10,
    # Absolute base URL for image links handed to devices, e.g. "http://192.168.1.10:8000".
    # When unset, it's built from the device's own request.
    "BASE_URL": None,
    # How many old renders to keep per dashboard/profile/orientation.
    "KEEP_RENDERS": 3,
}


def get(name):
    user = getattr(settings, "DJANGO_TRMNL", {}) or {}
    if name in user:
        return user[name]
    return DEFAULTS[name]
