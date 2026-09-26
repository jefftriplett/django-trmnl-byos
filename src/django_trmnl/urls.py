"""Include at the site root: TRMNL firmware calls ``<base url>/api/display``.

    path("", include("django_trmnl.urls")),
"""

from django.urls import path, re_path

from . import preview, views

app_name = "django_trmnl"

urlpatterns = [
    # Firmware calls these with and without a trailing slash; accept both, no redirects.
    re_path(r"^api/setup/?$", views.setup, name="setup"),
    re_path(r"^api/display/?$", views.display, name="display"),
    re_path(r"^api/log/?$", views.log, name="log"),
    path("api/images/<uuid:pk>.<str:extension>", views.image, name="image"),
    path(
        "api/placeholder/<str:friendly_id>/<str:state>.<str:extension>",
        views.placeholder,
        name="placeholder",
    ),
    re_path(r"^api/custom_plugins/(?P<uuid>[0-9a-f-]{36})/?$", views.webhook, name="webhook"),
    path("trmnl/", preview.index, name="preview-index"),
    path("trmnl/devices/<int:pk>/", preview.device, name="preview-device"),
    path("trmnl/dashboards/<int:pk>/", preview.dashboard, name="preview-dashboard"),
    path("trmnl/dashboards/<int:pk>/html/", preview.dashboard_html, name="preview-html"),
    path("trmnl/dashboards/<int:pk>/render/", preview.dashboard_render, name="preview-render"),
]
