from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(pattern_name="django_trmnl:preview-index"), name="home"),
    # At the root: the firmware calls <base url>/api/setup, /api/display and /api/log.
    path("", include("django_trmnl.urls")),
]
