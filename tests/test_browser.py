"""Tests for how the renderer gets hold of a browser."""

import re
from importlib.metadata import version
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from django_trmnl_byos.rendering import CONNECT_TIMEOUT_MS, Renderer


@pytest.fixture
def chromium(monkeypatch):
    playwright = MagicMock()
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: MagicMock(start=lambda: playwright))
    return playwright.chromium


def test_launches_locally_when_unconfigured(settings, chromium):
    """No browser container configured: run one in-process."""
    settings.DJANGO_TRMNL_BYOS = {"PLAYWRIGHT_WS_ENDPOINT": None}

    with Renderer():
        pass

    chromium.launch.assert_called_once_with()
    chromium.connect.assert_not_called()


def test_connects_when_configured(settings, chromium):
    """A configured endpoint is used instead of launching a browser."""
    settings.DJANGO_TRMNL_BYOS = {"PLAYWRIGHT_WS_ENDPOINT": "ws://browser:3000/"}

    with Renderer():
        pass

    chromium.connect.assert_called_once_with("ws://browser:3000/", timeout=CONNECT_TIMEOUT_MS)
    chromium.launch.assert_not_called()


def test_browser_image_matches_installed_client():
    """The browser server and the Python client must be the same version.

    A mismatch only shows up at runtime, as a failed websocket handshake on
    every render, so catch the drift here instead.
    """
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile.browser"
    match = re.search(r"^ARG PLAYWRIGHT_VERSION=(\S+)$", dockerfile.read_text(), re.MULTILINE)

    assert match, "Dockerfile.browser must declare ARG PLAYWRIGHT_VERSION=<version>"
    assert match.group(1) == version("playwright")
