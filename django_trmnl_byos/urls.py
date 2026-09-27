"""Include at the site root: TRMNL firmware calls ``<base url>/api/display``.

    path("", include("django_trmnl_byos.urls")),
"""

from django.urls import path, re_path

from . import editor, preview, views

app_name = "django_trmnl_byos"

urlpatterns = [
    # Firmware calls these with and without a trailing slash; accept both, no redirects.
    re_path(r"^api/setup/?$", views.setup, name="setup"),
    re_path(r"^api/display/?$", views.display, name="display"),
    re_path(r"^api/log/?$", views.log, name="log"),
    re_path(r"^api/current_screen/?$", views.current_screen, name="current-screen"),
    re_path(r"^api/display/current/?$", views.current_screen, name="display-current"),
    path("api/images/<uuid:pk>.<str:extension>", views.image, name="image"),
    path(
        "api/placeholder/<str:friendly_id>/<str:state>.<str:extension>",
        views.placeholder,
        name="placeholder",
    ),
    re_path(r"^api/custom_plugins/(?P<uuid>[0-9a-f-]{36})/?$", views.webhook, name="webhook"),
    path("trmnl/", preview.index, name="preview-index"),
    path("trmnl/dashboards/new/", editor.dashboard_edit, name="editor-dashboard-new"),
    path("trmnl/dashboards/<int:pk>/edit/", editor.dashboard_edit, name="editor-dashboard"),
    path("trmnl/dashboards/<int:pk>/delete/", editor.dashboard_delete, name="editor-dashboard-delete"),
    path("trmnl/plugins/", editor.plugin_list, name="editor-plugins"),
    path("trmnl/plugins/new/", editor.plugin_choose, name="editor-plugin-choose"),
    path("trmnl/plugins/new/<str:plugin_key>/", editor.plugin_edit, name="editor-plugin-new"),
    path("trmnl/plugins/<int:pk>/", editor.plugin_edit, name="editor-plugin"),
    path("trmnl/plugins/<int:pk>/refresh/", editor.plugin_refresh, name="editor-plugin-refresh"),
    path("trmnl/plugins/<int:pk>/delete/", editor.plugin_delete, name="editor-plugin-delete"),
    path("trmnl/devices/<int:pk>/", preview.device, name="preview-device"),
    path("trmnl/devices/<int:pk>/playlist/", preview.device_playlist, name="preview-device-playlist"),
    path("trmnl/dashboards/<int:pk>/", preview.dashboard, name="preview-dashboard"),
    path("trmnl/dashboards/<int:pk>/html/", preview.dashboard_html, name="preview-html"),
    path("trmnl/dashboards/<int:pk>/render/", preview.dashboard_render, name="preview-render"),
]
