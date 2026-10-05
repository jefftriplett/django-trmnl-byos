from django.http import HttpResponse
from django.views.generic.base import View

from django_trmnl_byos import __version__


class VersionView(View):
    """Plain-text version at /version.txt, which the workspace's `just check-versions` reads."""

    def get(self, request):
        return HttpResponse(__version__, content_type="text/plain")
