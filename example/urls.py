from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.generic import RedirectView
from health_check.views import HealthCheckView

from django_trmnl import __version__


def version(request):
    return JsonResponse({"version": __version__})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("apis/version/", version, name="version"),
    # Only the database check; the django-q2 worker is checked by healthcheck-worker.sh.
    path("health/", HealthCheckView.as_view(checks=["health_check.Database"]), name="health"),
    path("", RedirectView.as_view(pattern_name="django_trmnl:preview-index"), name="home"),
    # At the root: the firmware calls <base url>/api/setup, /api/display and /api/log.
    path("", include("django_trmnl.urls")),
]

if settings.DEBUG:
    urlpatterns += [path("__reload__/", include("django_browser_reload.urls"))]
