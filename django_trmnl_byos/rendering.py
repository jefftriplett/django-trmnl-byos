"""Screenshot dashboards with Playwright and store them as device-ready images."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from django.utils import timezone

from . import assets, conf
from .compose import compose
from .devices import get_profile
from .images import to_device_image
from .models import Render

logger = logging.getLogger(__name__)

READY = "() => window.TRMNL_PLUGINS_READY === true"


class Renderer:
    """Keeps one browser open across renders. Use as a context manager.

    GET requests to the framework host (the ~15 MB stylesheet, fonts, JS, icons)
    are answered from memory, then from the on-disk copy in ``assets`` (filled at
    build time by ``trmnl_fetch_assets``), and only fetched from the network
    when neither has them; a fetched file is saved to disk for next time.

    Playwright's sync API runs an event loop in its thread, and Django refuses
    ORM calls from a thread with a running loop. So every browser call runs on
    one dedicated thread, and the database work stays on the caller's thread.
    """

    def __init__(self):
        self._thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="trmnl-playwright")
        self._playwright = None
        self._browser = None
        self._cache = {}
        self._cache_hosts = assets.framework_hosts()

    def __enter__(self):
        self._thread.submit(self._start).result()
        return self

    def __exit__(self, *exc_info):
        self._thread.submit(self._stop).result()
        self._thread.shutdown()

    def _start(self):
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        endpoint = conf.get("PLAYWRIGHT_WS_ENDPOINT")
        if endpoint:
            self._browser = self._playwright.chromium.connect(endpoint)
        else:
            self._browser = self._playwright.chromium.launch()

    def _stop(self):
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def _route(self, route):
        request = route.request
        if request.method != "GET" or urlsplit(request.url).netloc not in self._cache_hosts:
            return route.continue_()
        cached = self._cache.get(request.url)
        if cached is None and (body := assets.read(request.url)) is not None:
            cached = {"status": 200, "headers": {"content-type": assets.content_type(request.url)}, "body": body}
            self._cache[request.url] = cached
        if cached is None:
            response = route.fetch()
            # body() is already decoded, so drop headers describing the compressed transfer.
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
            }
            cached = {"status": response.status, "headers": headers, "body": response.body()}
            if response.ok:
                self._cache[request.url] = cached
                try:
                    assets.write(request.url, cached["body"])
                except OSError:
                    logger.warning("Couldn't save %s to the asset cache", request.url, exc_info=True)
        route.fulfill(status=cached["status"], headers=cached["headers"], body=cached["body"])

    def screenshot(self, html, profile, orientation="landscape"):
        return self._thread.submit(self._screenshot, html, profile, orientation).result()

    def _screenshot(self, html, profile, orientation):
        # The framework scales .screen by --pixel-ratio itself (a CSS transform),
        # so the browser works in the panel's physical pixels at scale factor 1.
        width, height = profile.image_size(orientation)
        context = self._browser.new_context(viewport={"width": width, "height": height})
        try:
            context.route("**/*", self._route)
            page = context.new_page()
            page.set_content(html, wait_until="load")
            try:
                page.wait_for_function(READY, timeout=conf.get("RENDER_TIMEOUT"))
            except Exception:
                logger.warning("Framework runtime did not report ready; capturing anyway")
            page.evaluate("() => document.fonts.ready")
            return page.screenshot(type="png", clip={"x": 0, "y": 0, "width": width, "height": height})
        finally:
            context.close()

    def render(self, dashboard, profile_key, orientation="landscape"):
        profile = get_profile(profile_key)
        started = time.monotonic()
        html = compose(dashboard, profile.key, orientation)
        screenshot = self.screenshot(html, profile, orientation)
        image = to_device_image(screenshot, profile, orientation)
        fingerprint = Render.fingerprint_for(image)
        duration_ms = round((time.monotonic() - started) * 1000)

        latest = dashboard.latest_render(profile.key, orientation)
        if latest and latest.fingerprint == fingerprint:
            # Same pixels: keep the filename so the device skips a needless redraw.
            Render.objects.filter(pk=latest.pk).update(created_at=timezone.now(), duration_ms=duration_ms)
            latest.refresh_from_db()
            return latest

        render = Render.objects.create(
            dashboard=dashboard,
            profile=profile.key,
            orientation=orientation,
            image=image,
            fingerprint=fingerprint,
            html=html,
            duration_ms=duration_ms,
        )
        Render.prune(dashboard, profile.key, orientation)
        logger.info("Rendered %s for %s/%s in %sms", dashboard, profile.key, orientation, duration_ms)
        return render


def render_dashboard(dashboard, profile_key="og", orientation="landscape"):
    """Render one dashboard with a throwaway browser."""
    with Renderer() as renderer:
        return renderer.render(dashboard, profile_key, orientation)
