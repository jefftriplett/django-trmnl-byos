"""A local copy of the TRMNL Framework assets, so renders don't download them.

Every render loads the pinned Framework CSS (~15 MB), its JS, the fonts the CSS
points at, and plugin icons from the framework host. The worker used to fetch
all of that for each task. Instead, ``Renderer`` serves those requests from
files under ``ASSET_CACHE_DIR``:

- ``manage.py trmnl_fetch_assets`` fills the directory ahead of time (the
  Dockerfiles run it at build time, so a deployed worker never goes to the
  network for them);
- anything still missing is fetched once on first use and written there.

Files are stored under their URL's host and path, e.g.
``<dir>/trmnl.com/css/3.3.2/plugins.min.css``. The Framework version is part of
those paths, so bumping it fetches fresh copies rather than serving stale ones.
"""

import mimetypes
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from django.conf import settings

from . import conf

FONT_URL = re.compile(r"""url\(\s*['"]?(/fonts/[^'")\s]+)['"]?\s*\)""")


def cache_dir() -> Path:
    configured = conf.get("ASSET_CACHE_DIR")
    if configured:
        return Path(configured)
    base = getattr(settings, "BASE_DIR", None)
    return Path(base) / "trmnl_assets" if base else Path.cwd() / "trmnl_assets"


def framework_hosts() -> set[str]:
    return {urlsplit(conf.get("FRAMEWORK_CSS_URL")).netloc, urlsplit(conf.get("FRAMEWORK_JS_URL")).netloc}


def path_for(url: str) -> Path | None:
    """Where ``url`` is stored, or None for a URL that isn't a framework asset."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.netloc not in framework_hosts() or parts.query:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments or any(segment in (".", "..") for segment in segments):
        return None
    return cache_dir().joinpath(parts.netloc, *segments)


def content_type(url: str) -> str:
    guessed, _ = mimetypes.guess_type(urlsplit(url).path)
    return guessed or "application/octet-stream"


def read(url: str) -> bytes | None:
    path = path_for(url)
    if path is None or not path.is_file():
        return None
    return path.read_bytes()


def write(url: str, body: bytes) -> None:
    path = path_for(url)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write then rename, so a reader never sees half a file.
    partial = path.with_name(path.name + ".partial")
    partial.write_bytes(body)
    partial.replace(path)


def font_urls(css: str, css_url: str) -> list[str]:
    """Absolute URLs of the fonts a Framework stylesheet references."""
    return sorted({urljoin(css_url, match) for match in FONT_URL.findall(css)})


def prefetch_urls(css: str) -> list[str]:
    """Everything worth having before the first render: CSS, JS, fonts, plugin icons."""
    from .plugins import registry

    css_url = conf.get("FRAMEWORK_CSS_URL")
    icons = {plugin.icon for plugin in registry if getattr(plugin, "icon", None)}
    urls = [css_url, conf.get("FRAMEWORK_JS_URL"), *font_urls(css, css_url), *sorted(icons)]
    return [url for url in urls if path_for(url) is not None]
