"""The local copy of the TRMNL Framework assets that renders are served from."""

import io

import pytest
from django.core.management import call_command

from django_trmnl_byos import assets
from django_trmnl_byos.rendering import Renderer

CSS_URL = "https://trmnl.com/css/3.3.2/plugins.min.css"
CSS = b"""
@font-face{font-family:TRMNL12;src:url(/fonts/TRMNL12-Regular.woff2) format("woff2"),url('/fonts/TRMNL12-Regular.ttf')}
@font-face{font-family:Inter;src:url("/fonts/Inter.ttf")}
.x{background:url(data:image/svg+xml;charset=utf-8,%3Csvg%3E)}
"""


@pytest.fixture(autouse=True)
def asset_dir(settings, tmp_path):
    settings.DJANGO_TRMNL_BYOS = {"ASSET_CACHE_DIR": str(tmp_path)}
    return tmp_path


def test_files_are_stored_under_host_and_path(asset_dir):
    assert assets.path_for(CSS_URL) == asset_dir / "trmnl.com" / "css" / "3.3.2" / "plugins.min.css"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/fonts/Inter.ttf",  # not the framework host
        "https://trmnl.com/fonts/../../etc/passwd",  # path traversal
        "https://trmnl.com/fonts/Inter.ttf?v=2",  # query strings aren't cached
        "https://trmnl.com/",
        "data:font/woff2;base64,AAAA",
    ],
)
def test_other_urls_are_never_cached(url):
    assert assets.path_for(url) is None
    assets.write(url, b"nope")
    assert assets.read(url) is None


def test_write_then_read(asset_dir):
    assets.write(CSS_URL, CSS)
    assert assets.read(CSS_URL) == CSS
    assert not list(asset_dir.rglob("*.partial"))


def test_content_types():
    assert assets.content_type(CSS_URL) == "text/css"
    assert assets.content_type("https://trmnl.com/js/3.3.2/plugins.min.js") in {
        "text/javascript",
        "application/javascript",
    }
    assert assets.content_type("https://trmnl.com/fonts/TRMNL12-Regular.woff2") == "font/woff2"


def test_font_urls_come_from_the_stylesheet():
    assert assets.font_urls(CSS.decode(), CSS_URL) == [
        "https://trmnl.com/fonts/Inter.ttf",
        "https://trmnl.com/fonts/TRMNL12-Regular.ttf",
        "https://trmnl.com/fonts/TRMNL12-Regular.woff2",
    ]


def test_prefetch_covers_css_js_fonts_and_icons():
    urls = assets.prefetch_urls(CSS.decode())
    assert urls[:2] == [CSS_URL, "https://trmnl.com/js/3.3.2/plugins.min.js"]
    assert "https://trmnl.com/fonts/Inter.ttf" in urls
    assert "https://trmnl.com/images/plugins/trmnl--render.svg" in urls


class FakeRoute:
    """Just enough of Playwright's Route for Renderer._route."""

    def __init__(self, url, method="GET"):
        self.request = type("Request", (), {"url": url, "method": method})()
        self.fulfilled = None
        self.fetched = False
        self.continued = False

    def fulfill(self, **kwargs):
        self.fulfilled = kwargs

    def continue_(self):
        self.continued = True

    def fetch(self):
        self.fetched = True
        return type(
            "Response", (), {"status": 200, "ok": True, "headers": {"content-type": "text/css"}, "body": lambda s: CSS}
        )()


def test_renderer_serves_framework_assets_from_disk_without_the_network():
    assets.write(CSS_URL, CSS)
    route = FakeRoute(CSS_URL)
    Renderer()._route(route)
    assert not route.fetched
    assert route.fulfilled["body"] == CSS
    assert route.fulfilled["headers"]["content-type"] == "text/css"


def test_renderer_saves_a_fetched_asset_for_next_time():
    route = FakeRoute(CSS_URL)
    Renderer()._route(route)
    assert route.fetched
    assert assets.read(CSS_URL) == CSS


def test_renderer_leaves_other_requests_alone():
    route = FakeRoute("https://example.com/photo.png")
    Renderer()._route(route)
    assert route.continued and not route.fetched


def test_fetch_command_downloads_once(monkeypatch, asset_dir):
    downloaded = []

    def fake_urlopen(request, timeout):
        downloaded.append(request.full_url)
        return io.BytesIO(CSS if request.full_url == CSS_URL else b"file")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    call_command("trmnl_fetch_assets", stdout=io.StringIO())
    assert CSS_URL in downloaded
    assert "https://trmnl.com/fonts/Inter.ttf" in downloaded
    assert assets.read("https://trmnl.com/fonts/Inter.ttf") == b"file"

    downloaded.clear()
    call_command("trmnl_fetch_assets", stdout=io.StringIO())
    assert downloaded == []

    call_command("trmnl_fetch_assets", "--force", stdout=io.StringIO())
    assert CSS_URL in downloaded
