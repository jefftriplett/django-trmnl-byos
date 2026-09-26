"""Real renders with Playwright + Chromium. Run with: pytest -m render"""

import io

import pytest
from PIL import Image

from django_trmnl_byos.rendering import render_dashboard

pytestmark = [pytest.mark.render, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("profile, size, mode", [("og", (800, 480), "1"), ("x", (1872, 1404), "L")])
def test_render_fluid_mashup(fluid, profile, size, mode):
    render = render_dashboard(fluid, profile)
    image = Image.open(io.BytesIO(bytes(render.image)))
    assert image.size == size
    assert image.mode == mode
    assert "mashup--3x3" in render.html


def test_unchanged_content_keeps_its_filename(single):
    # Same pixels, same fingerprint: the device skips a needless redraw.
    first = render_dashboard(single, "og")
    second = render_dashboard(single, "og")
    assert second.pk == first.pk
    assert second.filename == first.filename
