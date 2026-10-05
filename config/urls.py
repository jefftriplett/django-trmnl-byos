from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.generic import RedirectView
from health_check.views import HealthCheckView

from django_trmnl_byos import __version__
from config.views import VersionView


def version(request):
    return JsonResponse({"version": __version__})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("apis/version/", version, name="version"),
    path("version.txt", VersionView.as_view(), name="version-txt"),
    # Only the database check; the django-q2 worker is checked by healthcheck-worker.sh.
    path("health/", HealthCheckView.as_view(checks=["health_check.Database"]), name="health"),
    path("", RedirectView.as_view(pattern_name="django_trmnl_byos:preview-index"), name="home"),
    # At the root: the firmware calls <base url>/api/setup, /api/display and /api/log.
    path("", include("django_trmnl_byos.urls")),
]

if settings.MCP_ENABLED:
    from config.mcp import server as mcp_server

    # Exactly /mcp, no trailing slash: it's the URL MCP clients (Claude's connector
    # among them) call, and a POST can't follow a redirect. Prepended so the app's
    # root-level routes below can't shadow it.
    urlpatterns = [
        path("mcp", mcp_server, name="mcp"),
        # OAuth for Claude Code, Claude.ai and ChatGPT: the authorization server's
        # endpoints, and its discovery documents under /.well-known/
        path("oauth/", include("django_mcpz.oauth.urls")),
        path("", include("django_mcpz.oauth.wellknown")),
        *urlpatterns,
    ]

if settings.DEBUG:
    urlpatterns += [path("__reload__/", include("django_browser_reload.urls"))]
