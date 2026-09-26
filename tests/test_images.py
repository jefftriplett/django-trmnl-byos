import io

import pytest
from PIL import Image

from django_trmnl.devices import PROFILES
from django_trmnl.images import encode_bmp, encode_png, placeholder, to_device_image


def gradient(width, height):
    return Image.linear_gradient("L").resize((width, height))


@pytest.mark.parametrize("bits", [1, 2, 4])
def test_png_is_grayscale_at_requested_depth(bits):
    data = encode_png(gradient(803, 20), bits)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert (data[24], data[25]) == (bits, 0)  # IHDR bit depth, colour type 0 (grayscale)
    image = Image.open(io.BytesIO(data))
    image.load()
    assert image.size == (803, 20)
    assert len(set(image.convert("L").tobytes())) == 2**bits


def test_bmp_matches_trmnl_og_format():
    data = encode_bmp(gradient(800, 480))
    assert data[:2] == b"BM"
    assert int.from_bytes(data[14:18], "little") == 40  # BITMAPINFOHEADER, i.e. BMP3
    assert int.from_bytes(data[28:30], "little") == 1  # 1 bit per pixel
    assert len(data) == 48062  # same size TRMNL's ImageMagick guide produces


def test_to_device_image_sizes():
    screenshot = io.BytesIO()
    gradient(1872, 1404).save(screenshot, format="PNG")
    data = to_device_image(screenshot.getvalue(), PROFILES["x"])
    assert Image.open(io.BytesIO(data)).size == (1872, 1404)
    portrait = to_device_image(screenshot.getvalue(), PROFILES["og"], "portrait")
    assert Image.open(io.BytesIO(portrait)).size == (480, 800)


def test_placeholder_formats():
    assert placeholder(PROFILES["og"], "landscape", "Hi", ["there"])[:4] == b"\x89PNG"
    assert placeholder(PROFILES["x"], "landscape", "Hi", ["there"])[:4] == b"\x89PNG"
